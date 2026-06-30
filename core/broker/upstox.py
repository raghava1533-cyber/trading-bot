"""
broker/upstox.py  -  Upstox API wrapper
Rate-limit safe: batches ALL LTP calls into 1-2 API calls per cycle.
Old: 40+ individual calls per cycle = 429 errors
New: 1 batch call for all strikes = zero 429 errors
"""
import csv, gzip, io, json, logging, os, time
from collections import defaultdict
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from upstox_client import Configuration, ApiClient
from upstox_client.api.market_quote_api import MarketQuoteApi
from upstox_client.api.order_api import OrderApi

load_dotenv()
log = logging.getLogger(__name__)

INDEX_KEYS = {
    "NIFTY":      "NSE_INDEX|Nifty 50",
    "BANKNIFTY":  "NSE_INDEX|Nifty Bank",
    "FINNIFTY":   "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|Nifty MidCap Select",
    "SENSEX":     "BSE_INDEX|SENSEX",
}

# Upstox rate limits: 200 req/min per endpoint
# We use 0.35s gap = ~2.8 req/sec = safe headroom
_MIN_CALL_GAP  = 0.35
_BATCH_SIZE    = 100
_MAX_RETRIES   = 4
_RETRY_DELAYS  = [2, 5, 10, 20]


def _load_token() -> str:
    try:
        from infra.redis_bus import get_data
        t = get_data("upstox_access_token")
        if t and len(t) > 20:
            os.environ["UPSTOX_ACCESS_TOKEN"] = t
            log.info("Token loaded from Redis")
            return t
    except Exception:
        pass
    t = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if t and len(t) > 20:
        log.info("Token loaded from env var")
        return t
    raise RuntimeError(
        "No Upstox token found. "
        "Open https://your-render-url.onrender.com/auth to login."
    )


class _RateLimiter:
    def __init__(self, min_gap=_MIN_CALL_GAP):
        self._gap  = min_gap
        self._last = 0.0
    def wait(self):
        elapsed = time.time() - self._last
        if elapsed < self._gap:
            time.sleep(self._gap - elapsed)
        self._last = time.time()


class Broker:
    def __init__(self):
        log.info("Upstox Broker initializing...")
        self.access_token = _load_token()
        cfg = Configuration()
        cfg.access_token = self.access_token
        self.api_client  = ApiClient(configuration=cfg)
        self.market_api  = MarketQuoteApi(self.api_client)
        self.order_api   = OrderApi(self.api_client)
        self._rl         = _RateLimiter()
        self._instrument_cache    = None
        self._instrument_cache_ts = 0.0
        self.instrument_file      = os.getenv("INSTRUMENT_CACHE_FILE", "instruments_cache.json")
        self._cache_ttl           = float(os.getenv("INSTRUMENT_CACHE_TTL_HOURS", "12")) * 3600

    # ── Batched LTP (1 call for up to 100 symbols) ────────────────────────────
    def _call_ltp(self, keys: list) -> dict:
        """Returns {instrument_key: last_price}. Handles 429 with backoff."""
        result = {}
        for i in range(0, len(keys), _BATCH_SIZE):
            batch = keys[i : i + _BATCH_SIZE]
            sym   = ",".join(batch)
            for attempt in range(_MAX_RETRIES):
                self._rl.wait()
                try:
                    try:
                        resp = self.market_api.ltp(symbol=sym, api_version="v2")
                    except TypeError:
                        resp = self.market_api.ltp(symbol=sym)
                    if resp and resp.data:
                        for k, v in resp.data.items():
                            result[k] = float(v.last_price)
                    break
                except Exception as exc:
                    s = str(exc)
                    if "429" in s or "Too Many" in s:
                        d = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)-1)]
                        log.warning(f"429 rate limit - waiting {d}s (attempt {attempt+1}/{_MAX_RETRIES})")
                        time.sleep(d)
                    else:
                        log.warning(f"LTP batch error attempt {attempt+1}: {exc}")
                        time.sleep(1)
        return result

    # ── Batched full quote (OI + IV) ──────────────────────────────────────────
    def _call_quote(self, keys: list) -> dict:
        """Returns {instrument_key: quote_obj}. Optional - used for OI/IV."""
        result = {}
        for i in range(0, len(keys), _BATCH_SIZE):
            batch = keys[i : i + _BATCH_SIZE]
            sym   = ",".join(batch)
            for attempt in range(_MAX_RETRIES):
                self._rl.wait()
                try:
                    resp = self.market_api.get_full_market_quote(symbol=sym, api_version="v2")
                    if resp and resp.data:
                        result.update(resp.data)
                    break
                except Exception as exc:
                    s = str(exc)
                    if "429" in s or "Too Many" in s:
                        d = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)-1)]
                        log.warning(f"429 on quote - waiting {d}s")
                        time.sleep(d)
                    else:
                        log.warning(f"Quote batch error attempt {attempt+1}: {exc}")
                        time.sleep(1)
        return result

    # ── Instrument master ─────────────────────────────────────────────────────
    def load_instruments(self) -> list:
        now = time.time()
        if self._instrument_cache and (now - self._instrument_cache_ts) < self._cache_ttl:
            return self._instrument_cache
        if os.path.exists(self.instrument_file):
            age = now - os.path.getmtime(self.instrument_file)
            if age < self._cache_ttl:
                try:
                    with open(self.instrument_file, "r") as f:
                        data = json.load(f)
                    self._instrument_cache    = data
                    self._instrument_cache_ts = now
                    log.info(f"Instruments loaded from cache ({len(data)} rows)")
                    return data
                except Exception:
                    log.warning("Instrument cache corrupt - re-downloading")
        url = os.getenv(
            "INSTRUMENT_MASTER_URL",
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
        )
        log.info("Downloading instrument master...")
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as gz:
                    content = gz.read().decode("utf-8")
                data = list(csv.DictReader(io.StringIO(content)))
                if not data:
                    raise ValueError("Empty CSV")
                with open(self.instrument_file, "w") as f:
                    json.dump(data, f)
                self._instrument_cache    = data
                self._instrument_cache_ts = now
                log.info(f"Instruments downloaded: {len(data)} rows")
                return data
            except Exception as exc:
                log.warning(f"Instrument download attempt {attempt+1} failed: {exc}")
                time.sleep(3)
        raise RuntimeError("Failed to load instrument master after 3 attempts")

    # ── Spot price ────────────────────────────────────────────────────────────
    def get_spot(self, symbol: str = "NIFTY", retries: int = 3):
        key = INDEX_KEYS.get(symbol.upper())
        if not key:
            log.error(f"No index key for {symbol}")
            return None
        for attempt in range(retries):
            prices = self._call_ltp([key])
            if prices:
                price = list(prices.values())[0]
                log.info(f"{symbol} spot = {price:,.2f}")
                return price
            d = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)-1)]
            log.warning(f"get_spot attempt {attempt+1} failed - retrying in {d}s")
            time.sleep(d)
        log.error(f"get_spot: all retries exhausted for {symbol}")
        return None

    # ── Nearest expiry ────────────────────────────────────────────────────────
    def get_nearest_expiry(self, symbol: str = "NIFTY"):
        try:
            instruments = self.load_instruments()
            today = datetime.now().strftime("%Y-%m-%d")
            exchange = "BSE_FO" if symbol.upper() == "SENSEX" else "NSE_FO"
            expiries = sorted({
                i["expiry"] for i in instruments
                if i.get("exchange") == exchange
                and i.get("name", "").upper() == symbol.upper()
                and i.get("instrument_type") == "OPTIDX"
                and i.get("expiry", "") >= today
            })
            if not expiries:
                log.error(f"No expiries found for {symbol}")
                return None
            log.info(f"Nearest expiry {symbol}: {expiries[0]}")
            return expiries[0]
        except Exception as exc:
            log.error(f"get_nearest_expiry: {exc}")
            return None

    # ── Option chain (BATCHED - 1-2 API calls total) ──────────────────────────
    def get_option_chain(self, symbol: str = "NIFTY", range_size: int = 1000):
        """
        BEFORE: 1 API call per strike = 40+ calls = 429 errors
        AFTER:  1 batch call for all strikes = 1-2 API calls total
        """
        try:
            from config import SETTINGS
            default_iv = SETTINGS.default_iv
            default_oi = SETTINGS.default_oi
        except Exception:
            default_iv = 0.18
            default_oi = 50000

        try:
            instruments = self.load_instruments()

            # 1 API call: spot
            spot = self.get_spot(symbol)
            if not spot:
                return [], None

            # 0 API calls: expiry from cache
            expiry = self.get_nearest_expiry(symbol)
            if not expiry:
                return [], None

            log.info(f"Building chain {symbol} | expiry={expiry} | spot={spot:,.0f}")

            # 0 API calls: filter from instrument master
            exchange = "BSE_FO" if symbol.upper() == "SENSEX" else "NSE_FO"
            options = [
                i for i in instruments
                if i.get("exchange") == exchange
                and i.get("name", "").upper() == symbol.upper()
                and i.get("instrument_type") == "OPTIDX"
                and i.get("expiry") == expiry
                and i.get("strike")
                and i.get("instrument_key")
                and abs(float(i["strike"]) - spot) <= range_size
            ]

            if not options:
                log.warning(f"No options found for {symbol} expiry={expiry}")
                return [], spot

            log.info(f"Fetching LTP for {len(options)} options (batched)...")

            # 1-2 API calls: ALL strikes in one batch
            all_keys = [o["instrument_key"] for o in options]
            ltp_map  = self._call_ltp(all_keys)

            # 1-2 API calls: quotes for OI/IV (optional)
            quote_map = {}
            try:
                quote_map = self._call_quote(all_keys)
            except Exception as e:
                log.warning(f"Quote batch skipped (using defaults): {e}")

            # Build chain from results
            grouped = defaultdict(lambda: {"strikePrice": None, "CE": {}, "PE": {}})
            for opt in options:
                strike = float(opt["strike"])
                side   = opt.get("option_type")
                ikey   = opt.get("instrument_key", "")
                if side not in ("CE", "PE"):
                    continue
                ltp = ltp_map.get(ikey)
                if ltp is None:
                    continue
                oi, iv = default_oi, default_iv
                q = quote_map.get(ikey)
                if q:
                    try:
                        oi = int(getattr(q, "oi", 0) or 0) or default_oi
                        iv_raw = getattr(q, "implied_volatility", None)
                        iv = float(iv_raw) / 100.0 if iv_raw else default_iv
                    except Exception:
                        pass
                sym = opt.get("tradingsymbol", "").strip() or \
                      f"{symbol}{expiry.replace('-','')}{int(strike)}{side}"
                grouped[strike]["strikePrice"] = strike
                grouped[strike][side] = {
                    "ltp": ltp, "iv": iv, "oi": oi,
                    "tradingsymbol": sym, "instrument_key": ikey,
                }

            chain = sorted(
                [v for v in grouped.values() if v["strikePrice"] is not None],
                key=lambda x: x["strikePrice"],
            )
            log.info(f"Chain built: {len(chain)} strikes | {len(ltp_map)} prices fetched")
            return chain, spot

        except Exception as exc:
            log.error(f"get_option_chain: {exc}")
            return [], None

    # ── Historical candles ────────────────────────────────────────────────────
    def get_candles(self, symbol: str = "NIFTY", interval: str = "day", days: int = 365):
        import pandas as pd
        key = INDEX_KEYS.get(symbol.upper())
        if not key:
            log.warning(f"get_candles: no index key for {symbol}")
            return None
        encoded_key = key.replace("|", "%7C")
        to_date   = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = (
            f"https://api.upstox.com/v2/historical-candle/{encoded_key}"
            f"/{interval}/{to_date}/{from_date}"
        )
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        for attempt in range(_MAX_RETRIES):
            try:
                self._rl.wait()
                resp = requests.get(url, headers=headers,
                                    timeout=int(os.getenv("HTTP_TIMEOUT_SECONDS", "15")))
                if resp.status_code == 429:
                    d = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)-1)]
                    log.warning(f"429 on candles - waiting {d}s")
                    time.sleep(d)
                    continue
                resp.raise_for_status()
                candles = resp.json().get("data", {}).get("candles", [])
                if not candles:
                    log.warning(f"get_candles: empty response for {symbol}")
                    return None
                df = __import__("pandas").DataFrame(
                    candles, columns=["timestamp","open","high","low","close","volume","oi"])
                df["timestamp"] = __import__("pandas").to_datetime(df["timestamp"])
                df = df.drop(columns=["oi"], errors="ignore").sort_values("timestamp").reset_index(drop=True)
                log.info(f"get_candles: {symbol} {len(df)} bars via Upstox")
                return df
            except Exception as exc:
                log.warning(f"get_candles attempt {attempt+1}: {exc}")
                time.sleep(2)
        return None

    # ── Order placement ───────────────────────────────────────────────────────
    def place_order(self, instrument_key: str, side: str, qty: int, price=None):
        from config import SETTINGS
        if SETTINGS.dry_run:
            log.info(f"[DRY RUN] place_order {side} {qty} @ {instrument_key}")
            return {"dry_run": True, "instrument_key": instrument_key, "side": side, "qty": qty}
        for attempt in range(int(os.getenv("API_RETRIES", "3"))):
            try:
                self._rl.wait()
                order = {
                    "quantity": qty, "product": os.getenv("ORDER_PRODUCT", "D"),
                    "validity": os.getenv("ORDER_VALIDITY", "DAY"),
                    "instrument_token": instrument_key,
                    "order_type": "MARKET" if price is None else "LIMIT",
                    "transaction_type": side.upper(),
                    "price": price or 0,
                    "tag": os.getenv("ORDER_TAG", "algo_bot"),
                }
                resp = self.order_api.place_order(order)
                log.info(f"Order placed: {resp}")
                return resp
            except Exception as exc:
                log.warning(f"place_order attempt {attempt+1}: {exc}")
                time.sleep(float(os.getenv("API_RETRY_SLEEP_SECONDS", "1")))
        log.error("place_order: all retries exhausted")
        return None

    def get_positions(self) -> list:
        return []

    def logout(self) -> None:
        log.info("Broker logout")