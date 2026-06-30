"""
broker/upstox.py  —  Upstox API wrapper
Provides: get_spot, get_nearest_expiry, get_option_chain, get_candles, place_order
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


def _load_token() -> str:
    """
    Load Upstox token with priority:
    1. Redis  (saved by /auth/callback — persists across restarts)
    2. Env var UPSTOX_ACCESS_TOKEN (set in Render dashboard)
    Raises RuntimeError if neither found.
    """
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


class Broker:
    def __init__(self):
        log.info("Upstox Broker initializing...")
        self.access_token = _load_token()

        cfg = Configuration()
        cfg.access_token = self.access_token
        self.api_client  = ApiClient(configuration=cfg)
        self.market_api  = MarketQuoteApi(self.api_client)
        self.order_api   = OrderApi(self.api_client)

        self._instrument_cache     = None
        self._instrument_cache_ts  = 0.0
        self.instrument_file       = os.getenv("INSTRUMENT_CACHE_FILE", "instruments_cache.json")
        self._cache_ttl            = float(os.getenv("INSTRUMENT_CACHE_TTL_HOURS", "12")) * 3600

    # ── Internal helpers ──────────────────────────────────────────────────────
    def _safe_ltp(self, keys: list[str]):
        k = ",".join(keys)
        try:
            return self.market_api.ltp(symbol=k, api_version="v2")
        except TypeError:
            return self.market_api.ltp(symbol=k)

    def _safe_quote(self, keys: list[str]):
        try:
            k = ",".join(keys)
            return self.market_api.get_full_market_quote(symbol=k, api_version="v2")
        except Exception:
            return None

    # ── Instrument master ─────────────────────────────────────────────────────
    def load_instruments(self) -> list[dict]:
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
                    log.warning("Instrument cache corrupt — re-downloading")

        url = os.getenv(
            "INSTRUMENT_MASTER_URL",
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
        )
        log.info("Downloading instrument master...")
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=15)
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
                time.sleep(2)
        raise RuntimeError("Failed to load instrument master after 3 attempts")

    # ── Spot price ────────────────────────────────────────────────────────────
    def get_spot(self, symbol: str = "NIFTY", retries: int = 3) -> float | None:
        key = INDEX_KEYS.get(symbol.upper())
        if not key:
            log.error(f"No index key for {symbol}")
            return None
        for attempt in range(retries):
            try:
                resp = self._safe_ltp([key])
                if resp and resp.data:
                    price = float(list(resp.data.values())[0].last_price)
                    log.info(f"{symbol} spot = {price:,.2f}")
                    return price
            except Exception as exc:
                log.warning(f"get_spot attempt {attempt+1}: {exc}")
                time.sleep(2)
        log.error(f"get_spot: all retries exhausted for {symbol}")
        return None

    # ── Nearest expiry ────────────────────────────────────────────────────────
    def get_nearest_expiry(self, symbol: str = "NIFTY") -> str | None:
        try:
            instruments = self.load_instruments()
            expiries = sorted({
                i["expiry"] for i in instruments
                if i.get("exchange") == "NSE_FO"
                and i.get("name", "").upper() == symbol.upper()
                and i.get("instrument_type") == "OPTIDX"
                and i.get("expiry")
            })
            if not expiries:
                log.error(f"No expiries found for {symbol}")
                return None
            log.info(f"Nearest expiry {symbol}: {expiries[0]}")
            return expiries[0]
        except Exception as exc:
            log.error(f"get_nearest_expiry: {exc}")
            return None

    # ── Option chain ──────────────────────────────────────────────────────────
    def get_option_chain(self, symbol: str = "NIFTY", range_size: int = 1000):
        try:
            from config import SETTINGS
            default_iv = SETTINGS.default_iv
            default_oi = SETTINGS.default_oi
        except Exception:
            default_iv = 0.18
            default_oi = 50000

        try:
            instruments = self.load_instruments()
            spot = self.get_spot(symbol)
            if not spot:
                return [], None

            expiry = self.get_nearest_expiry(symbol)
            if not expiry:
                return [], None

            log.info(f"Building chain {symbol} | expiry={expiry} | spot={spot:,.0f}")

            options = [
                i for i in instruments
                if i.get("exchange") == "NSE_FO"
                and i.get("name", "").upper() == symbol.upper()
                and i.get("instrument_type") == "OPTIDX"
                and i.get("expiry") == expiry
                and i.get("strike")
                and abs(float(i["strike"]) - spot) <= range_size
            ]

            grouped: dict = defaultdict(lambda: {"strikePrice": None, "CE": {}, "PE": {}})

            for opt in options:
                strike = float(opt["strike"])
                side   = opt.get("option_type")
                if side not in ("CE", "PE"):
                    continue
                try:
                    resp = self._safe_ltp([opt["instrument_key"]])
                    if not resp or not resp.data:
                        continue
                    ltp = float(list(resp.data.values())[0].last_price)

                    oi, iv = default_oi, default_iv
                    try:
                        full = self._safe_quote([opt["instrument_key"]])
                        if full and full.data:
                            q  = list(full.data.values())[0]
                            oi = int(getattr(q, "oi", 0) or 0) or default_oi
                            iv_raw = getattr(q, "implied_volatility", None)
                            iv = float(iv_raw) / 100.0 if iv_raw else default_iv
                    except Exception:
                        pass

                    sym = opt.get("tradingsymbol", "").strip()
                    if not sym:
                        sym = f"{symbol}{expiry.replace('-','')}{int(strike)}{side}"

                    grouped[strike]["strikePrice"] = strike
                    grouped[strike][side] = {
                        "ltp":            ltp,
                        "iv":             iv,
                        "oi":             oi,
                        "tradingsymbol":  sym,
                        "instrument_key": opt["instrument_key"],
                    }
                    time.sleep(float(os.getenv("QUOTE_THROTTLE_SECONDS", "0.05")))
                except Exception as exc:
                    log.warning(f"Skipping strike {strike} {side}: {exc}")

            chain = sorted(
                [v for v in grouped.values() if v["strikePrice"] is not None],
                key=lambda x: x["strikePrice"],
            )
            log.info(f"Chain built: {len(chain)} strikes for {symbol}")
            return chain, spot

        except Exception as exc:
            log.error(f"get_option_chain: {exc}")
            return [], None

    # ── Historical candles ────────────────────────────────────────────────────
    def get_candles(self, symbol: str = "NIFTY", interval: str = "day",
                    days: int = 365) -> "pd.DataFrame | None":
        """
        Fetch OHLCV candles from Upstox Historical Data API.
        interval: 'day' | '30minute' | 'week' | 'month'
        Returns a DataFrame with columns: timestamp, open, high, low, close, volume
        """
        import pandas as pd

        key = INDEX_KEYS.get(symbol.upper())
        if not key:
            log.warning(f"get_candles: no index key for {symbol}")
            return None

        # Upstox historical API uses instrument_key with | replaced by %7C
        encoded_key = key.replace("|", "%7C")
        to_date   = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        url = (
            f"https://api.upstox.com/v2/historical-candle/{encoded_key}"
            f"/{interval}/{to_date}/{from_date}"
        )
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept":        "application/json",
        }

        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers,
                                    timeout=int(os.getenv("HTTP_TIMEOUT_SECONDS", "15")))
                resp.raise_for_status()
                data = resp.json()
                candles = data.get("data", {}).get("candles", [])
                if not candles:
                    log.warning(f"get_candles: empty response for {symbol}")
                    return None

                df = pd.DataFrame(candles,
                                  columns=["timestamp", "open", "high", "low",
                                           "close", "volume", "oi"])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = (df.drop(columns=["oi"], errors="ignore")
                        .sort_values("timestamp")
                        .reset_index(drop=True))
                log.info(f"get_candles: {symbol} {len(df)} bars via Upstox")
                return df
            except Exception as exc:
                log.warning(f"get_candles attempt {attempt+1}: {exc}")
                time.sleep(2)
        return None

    # ── Order placement ───────────────────────────────────────────────────────
    def place_order(self, instrument_key: str, side: str, qty: int,
                    price: float | None = None) -> dict | None:
        from config import SETTINGS
        if SETTINGS.dry_run:
            log.info(f"[DRY RUN] place_order {side} {qty} @ {instrument_key}")
            return {"dry_run": True, "instrument_key": instrument_key,
                    "side": side, "qty": qty}
        for attempt in range(int(os.getenv("API_RETRIES", "3"))):
            try:
                order = {
                    "quantity":         qty,
                    "product":          os.getenv("ORDER_PRODUCT", "D"),
                    "validity":         os.getenv("ORDER_VALIDITY", "DAY"),
                    "instrument_token": instrument_key,
                    "order_type":       "MARKET" if price is None else "LIMIT",
                    "transaction_type": side.upper(),
                    "price":            price or 0,
                    "tag":              os.getenv("ORDER_TAG", "algo_bot"),
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

