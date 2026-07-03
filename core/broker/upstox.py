"""
broker/upstox.py  -  Upstox API wrapper
Rate limit: Upstox allows ~1 req/sec. All calls go through _rate_limited_call().
"""
import csv, gzip, io, json, logging, os, time, threading
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
    "BANKEX":     "BSE_INDEX|BSE Bankex",
}
_EXCHANGE = {
    "NIFTY": "NSE_FO", "BANKNIFTY": "NSE_FO",
    "FINNIFTY": "NSE_FO", "MIDCPNIFTY": "NSE_FO",
    "SENSEX": "BSE_FO", "BANKEX": "BSE_FO",
}

# Global rate limiter - ONE lock shared across all Broker instances
_rate_lock      = threading.Lock()
_last_call_ts   = 0.0
_MIN_GAP        = 1.2   # seconds between any two Upstox API calls


def _rate_limited_call(fn, *args, **kwargs):
    """Enforce min gap + exponential backoff on 429."""
    global _last_call_ts
    with _rate_lock:
        gap = time.time() - _last_call_ts
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _last_call_ts = time.time()
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "Too Many" in msg:
                wait = min(2 ** attempt * 3, 60)   # 3,6,12,24,60s
                log.warning(f"429 rate limit - backing off {wait}s (attempt {attempt+1}/5)")
                time.sleep(wait)
                with _rate_lock:
                    _last_call_ts = time.time()
            else:
                raise
    raise RuntimeError("Rate limit: 5 retries exhausted")


def _load_token() -> str:
    try:
        from infra.redis_bus import get_data
        t = get_data("upstox_access_token")
        if t and len(t) > 20:
            os.environ["UPSTOX_ACCESS_TOKEN"] = t
            return t
    except Exception:
        pass
    t = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if t and len(t) > 20:
        return t
    raise RuntimeError("No Upstox token. Open /auth to login.")


class Broker:
    def __init__(self):
        log.info("Broker initializing...")
        self.access_token = _load_token()
        cfg = Configuration()
        cfg.access_token = self.access_token
        self.api_client  = ApiClient(configuration=cfg)
        self.market_api  = MarketQuoteApi(self.api_client)
        self.order_api   = OrderApi(self.api_client)
        self._instrument_cache    = None
        self._instrument_cache_ts = 0.0
        self.instrument_file      = os.getenv("INSTRUMENT_CACHE_FILE", "instruments_cache.json")
        self._cache_ttl           = float(os.getenv("INSTRUMENT_CACHE_TTL_HOURS", "12")) * 3600
        self._spot_cache: dict    = {}   # {symbol: (price, ts)}
        self._spot_cache_ttl      = 30.0
        log.info("Broker ready")

    def _ltp(self, keys: list):
        """Rate-limited LTP call."""
        k = ",".join(keys)
        def _call():
            try:
                return self.market_api.ltp(symbol=k, api_version="v2")
            except TypeError:
                return self.market_api.ltp(symbol=k)
        return _rate_limited_call(_call)

    def _cache_has_valid_expiry(self, data: list) -> bool:
        """Return False if ALL expiries in cache are in the past (stale cache)."""
        today = datetime.now().strftime("%Y-%m-%d")
        expiries = {i["expiry"] for i in data if i.get("expiry") and i.get("instrument_type") == "OPTIDX"}
        future = [e for e in expiries if e >= today]
        return len(future) > 0

    def load_instruments(self) -> list:
        now = time.time()
        if self._instrument_cache and (now - self._instrument_cache_ts) < self._cache_ttl:
            return self._instrument_cache
        if os.path.exists(self.instrument_file):
            age = now - os.path.getmtime(self.instrument_file)
            if age < self._cache_ttl:
                try:
                    with open(self.instrument_file, encoding="utf-8") as _f:
                        data = json.load(_f)
                    if self._cache_has_valid_expiry(data):
                        self._instrument_cache    = data
                        self._instrument_cache_ts = now
                        log.info(f"Instruments from cache: {len(data)} rows")
                        return data
                    else:
                        log.info("Instruments cache has only expired expiries - refreshing")
                except Exception:
                    pass
        url = os.getenv("INSTRUMENT_MASTER_URL",
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz")
        log.info("Downloading instrument master...")
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as gz:
                    content = gz.read().decode("utf-8")
                data = list(csv.DictReader(io.StringIO(content)))
                with open(self.instrument_file, "w", encoding="utf-8") as _fw:
                    json.dump(data, _fw)
                self._instrument_cache    = data
                self._instrument_cache_ts = now
                log.info(f"Instruments downloaded: {len(data)} rows")
                return data
            except Exception as exc:
                log.warning(f"Instrument download attempt {attempt+1}: {exc}")
                time.sleep(5)
        raise RuntimeError("Failed to load instruments")

    def get_spot(self, symbol: str = "NIFTY") -> float | None:
        sym = symbol.upper()
        cached = self._spot_cache.get(sym)
        if cached and time.time() - cached[1] < self._spot_cache_ttl:
            return cached[0]
        key = INDEX_KEYS.get(sym)
        if not key:
            log.error(f"No index key for {sym}")
            return None
        try:
            resp = self._ltp([key])
            if resp and resp.data:
                price = float(list(resp.data.values())[0].last_price)
                self._spot_cache[sym] = (price, time.time())
                log.info(f"{sym} spot = {price:,.2f}")
                return price
        except Exception as exc:
            log.error(f"get_spot {sym}: {exc}")
        return None

    def get_spot_batch(self, symbols: list) -> dict:
        """
        Fetch spot prices for all symbols.
        Tries a single batch call first; if key matching fails,
        falls back to sequential calls (rate limiter handles pacing).
        """
        keys, keymap = [], {}
        for sym in symbols:
            k = INDEX_KEYS.get(sym.upper())
            if k:
                keys.append(k)
                # Build multiple match variants for robust lookup
                # Upstox batch LTP responds with colon separator (NSE_INDEX:Nifty 50)
                # but we send pipe separator (NSE_INDEX|Nifty 50)
                keymap[k]                      = sym.upper()   # pipe: NSE_INDEX|Nifty 50
                keymap[k.replace("|", ":")]    = sym.upper()   # colon: NSE_INDEX:Nifty 50
                keymap[k.replace("|", "%7C")]  = sym.upper()   # url-encoded
                keymap[k.lower()]              = sym.upper()   # lowercase
                keymap[k.replace("|", ":").lower()] = sym.upper()  # colon lowercase
        if not keys:
            return {}
        result = {}
        try:
            resp = self._ltp(keys)
            if resp and resp.data:
                for raw_key, v in resp.data.items():
                    price = float(v.last_price)
                    # Try exact match first
                    sym = keymap.get(raw_key)
                    if not sym:
                        # Try partial match - find which INDEX_KEY is contained in raw_key
                        for ik, s in keymap.items():
                            clean_ik  = ik.replace("%7C", "|").lower()
                            clean_raw = raw_key.replace("%7C", "|").lower()
                            if clean_ik in clean_raw or clean_raw in clean_ik:
                                sym = s
                                break
                    if sym:
                        result[sym] = price
                        self._spot_cache[sym] = (price, time.time())
                        log.info(f"  {sym} spot = {price:,.2f}")
                    else:
                        log.warning(f"  Could not match key: {raw_key}")
        except Exception as exc:
            log.error(f"get_spot_batch failed: {exc}")

        # For any symbols not matched, fetch individually
        missing = [s for s in symbols if s.upper() not in result]
        if missing:
            log.info(f"  Fetching individually: {missing}")
            for sym in missing:
                p = self.get_spot(sym)
                if p:
                    result[sym.upper()] = p
        return result

    def get_nearest_expiry(self, symbol: str = "NIFTY") -> str | None:
        try:
            instruments = self.load_instruments()
            exchange    = _EXCHANGE.get(symbol.upper(), "NSE_FO")
            expiries    = sorted({
                i["expiry"] for i in instruments
                if i.get("exchange") == exchange
                and i.get("name", "").upper() == symbol.upper()
                and i.get("instrument_type") == "OPTIDX"
                and i.get("expiry")
            })
            if not expiries:
                log.error(f"No expiries for {symbol} on {exchange}")
                return None
            log.info(f"Nearest expiry {symbol}: {expiries[0]}")
            return expiries[0]
        except Exception as exc:
            log.error(f"get_nearest_expiry: {exc}")
            return None

    def get_option_chain(self, symbol: str = "NIFTY", range_size: int = 1000,
                         spot: float | None = None):
        """Build option chain. Pass spot= to skip extra API call."""
        try:
            from config import SETTINGS
            default_iv, default_oi = SETTINGS.default_iv, SETTINGS.default_oi
        except Exception:
            default_iv, default_oi = 0.18, 50000
        try:
            instruments = self.load_instruments()
            if spot is None:
                spot = self.get_spot(symbol)
            if not spot:
                log.warning(f"[{symbol}] No spot price available")
                return [], None
            expiry = self.get_nearest_expiry(symbol)
            if not expiry:
                return [], None
            exchange = _EXCHANGE.get(symbol.upper(), "NSE_FO")
            options  = [
                i for i in instruments
                if i.get("exchange") == exchange
                and i.get("name", "").upper() == symbol.upper()
                and i.get("instrument_type") == "OPTIDX"
                and i.get("expiry") == expiry
                and i.get("strike")
                and abs(float(i["strike"]) - spot) <= range_size
            ]
            if not options:
                log.error(f"[{symbol}] No options on {exchange} expiry={expiry}")
                return [], None
            log.info(f"[{symbol}] {len(options)} contracts | expiry={expiry} | spot={spot:,.0f}")
            # Send instrument_key (NSE_FO|44699) to API
            # API responds with exchange:tradingsymbol (NSE_FO:NIFTY2670725350PE)
            # Build reverse map: "EXCHANGE:TRADINGSYMBOL" -> option row
            exchange = _EXCHANGE.get(symbol.upper(), "NSE_FO")
            resp_key_to_opt = {
                f"{exchange}:{o['tradingsymbol']}": o for o in options
            }
            all_ikeys = [o["instrument_key"] for o in options]

            ltp_data: dict = {}   # "EXCHANGE:TRADINGSYMBOL" -> price
            for i in range(0, len(all_ikeys), 200):
                batch = all_ikeys[i:i+200]
                try:
                    resp = self._ltp(batch)
                    if resp and resp.data:
                        if i == 0:
                            sample_resp = next(iter(resp.data.keys()))
                            log.info(f"[{symbol}] Sent: {batch[0]!r} -> Got: {sample_resp!r}")
                        for k, v in resp.data.items():
                            ltp_data[k] = float(v.last_price)
                    log.info(f"[{symbol}] LTP batch {i//200+1}: {len(ltp_data)} prices fetched")
                except Exception as exc:
                    log.warning(f"[{symbol}] LTP batch failed: {exc}")

            grouped: dict = defaultdict(lambda: {"strikePrice": None, "CE": {}, "PE": {}})
            matched = 0
            for resp_key, opt in resp_key_to_opt.items():
                strike = float(opt["strike"])
                side   = opt.get("option_type")
                if side not in ("CE", "PE"):
                    continue
                ltp = ltp_data.get(resp_key)
                if ltp is None:
                    continue
                matched += 1
                sym = opt.get("tradingsymbol", "").strip() or \
                      f"{symbol}{expiry.replace('-','')}{int(strike)}{side}"
                grouped[strike]["strikePrice"] = strike
                grouped[strike][side] = {
                    "ltp": ltp, "iv": default_iv, "oi": default_oi,
                    "tradingsymbol": sym, "instrument_key": opt["instrument_key"],
                }
            chain = sorted(
                [v for v in grouped.values() if v["strikePrice"] is not None],
                key=lambda x: x["strikePrice"],
            )
            log.info(f"[{symbol}] Chain ready: {len(chain)} strikes (matched {matched}/{len(options)} contracts)")
            return chain, spot
        except Exception as exc:
            log.error(f"get_option_chain {symbol}: {exc}")
            return [], None

    def get_candles(self, symbol: str = "NIFTY", interval: str = "day", days: int = 365):
        import pandas as pd
        key = INDEX_KEYS.get(symbol.upper())
        if not key:
            return None
        encoded = key.replace("|", "%7C")
        to_dt   = datetime.now().strftime("%Y-%m-%d")
        fr_dt   = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        url     = f"https://api.upstox.com/v2/historical-candle/{encoded}/{interval}/{to_dt}/{fr_dt}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                candles = resp.json().get("data", {}).get("candles", [])
                if not candles:
                    return None
                df = pd.DataFrame(candles,
                    columns=["timestamp","open","high","low","close","volume","oi"])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.drop(columns=["oi"], errors="ignore").sort_values("timestamp").reset_index(drop=True)
                log.info(f"get_candles: {symbol} {len(df)} bars")
                return df
            except Exception as exc:
                log.warning(f"get_candles attempt {attempt+1}: {exc}")
                time.sleep(3)
        return None

    def place_order(self, instrument_key: str, side: str, qty: int,
                    price: float | None = None) -> dict | None:
        from config import SETTINGS
        if SETTINGS.dry_run:
            log.info(f"[DRY RUN] {side} {qty} @ {instrument_key}")
            return {"dry_run": True, "instrument_key": instrument_key, "side": side, "qty": qty}
        for attempt in range(int(os.getenv("API_RETRIES", "3"))):
            try:
                order = {
                    "quantity": qty, "product": os.getenv("ORDER_PRODUCT", "D"),
                    "validity": os.getenv("ORDER_VALIDITY", "DAY"),
                    "instrument_token": instrument_key,
                    "order_type": "MARKET" if price is None else "LIMIT",
                    "transaction_type": side.upper(), "price": price or 0,
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
