import os
import gzip
import csv
import io
import time
import json
import logging
import requests
from collections import defaultdict
from dotenv import load_dotenv

from upstox_client import Configuration, ApiClient
from upstox_client.api.market_quote_api import MarketQuoteApi
from upstox_client.api.order_api import OrderApi

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class Broker:
    INDEX_KEYS = {
        "NIFTY":      "NSE_INDEX|Nifty 50",
        "BANKNIFTY":  "NSE_INDEX|Nifty Bank",
        "FINNIFTY":   "NSE_INDEX|Nifty Fin Service",
        "MIDCPNIFTY": "NSE_INDEX|Nifty MidCap Select",
        "SENSEX":     "BSE_INDEX|SENSEX",
    }

    def __init__(self):
        logging.info("✅ Upstox Broker initializing...")
        self.access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
        if not self.access_token:
            raise Exception("❌ Missing UPSTOX_ACCESS_TOKEN")

        config = Configuration()
        config.access_token = self.access_token
        self.api_client = ApiClient(configuration=config)
        self.market_api = MarketQuoteApi(self.api_client)
        self.order_api = OrderApi(self.api_client)
        self.instrument_cache = None
        self.instrument_file = "instruments_cache.json"

    def safe_ltp(self, keys):
        try:
            if isinstance(keys, list):
                keys = ",".join(keys)
            return self.market_api.ltp(symbol=keys, api_version="v2")
        except TypeError:
            if isinstance(keys, list):
                keys = ",".join(keys)
            return self.market_api.ltp(symbol=keys)

    def safe_quote(self, keys):
        try:
            if isinstance(keys, list):
                keys = ",".join(keys)
            return self.market_api.get_full_market_quote(symbol=keys, api_version="v2")
        except Exception:
            return None

    def load_instruments(self):
        if self.instrument_cache:
            return self.instrument_cache

        if os.path.exists(self.instrument_file):
            try:
                with open(self.instrument_file, "r") as f:
                    data = json.load(f)
                    self.instrument_cache = data
                    logging.info(f"✅ Loaded instruments from file ({len(data)})")
                    return data
            except Exception:
                logging.warning("⚠️ Failed to read local cache, re-downloading...")

        logging.info("📥 Downloading instrument master...")
        url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
        for attempt in range(3):
            try:
                response = requests.get(url, timeout=15)
                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")
                if not response.content:
                    raise Exception("Empty response")
                compressed = io.BytesIO(response.content)
                with gzip.GzipFile(fileobj=compressed) as f:
                    content = f.read().decode("utf-8")
                reader = csv.DictReader(io.StringIO(content))
                data = [row for row in reader]
                if not data:
                    raise Exception("Empty CSV after parsing")
                with open(self.instrument_file, "w") as f:
                    json.dump(data, f)
                self.instrument_cache = data
                logging.info(f"✅ Instruments loaded: {len(data)}")
                return data
            except Exception as e:
                logging.warning(f"[Retry {attempt+1}] Instrument load failed: {e}")
                time.sleep(1)
        raise Exception("❌ Failed to load instrument master")

    def get_spot(self, symbol="NIFTY", retries=3):
        try:
            instrument_key = self.INDEX_KEYS.get(symbol.upper())
            if not instrument_key:
                raise Exception(f"No index key configured for symbol: {symbol}")
            for attempt in range(retries):
                try:
                    response = self.safe_ltp([instrument_key])
                    if response and response.data:
                        ltp_obj = list(response.data.values())[0]
                        price = float(ltp_obj.last_price)
                        logging.info(f"📊 {symbol} Spot = {price}")
                        return price
                except Exception as e:
                    logging.warning(f"[Retry {attempt+1}] get_spot failed: {e}")
                    time.sleep(2)
            logging.error(f"❌ get_spot: all retries exhausted for {symbol}")
            return None
        except Exception as e:
            logging.error(f"❌ get_spot error: {e}")
            return None

    def get_nearest_expiry(self, symbol="NIFTY"):
        try:
            instruments = self.load_instruments()
            expiries = sorted({
                item.get("expiry")
                for item in instruments
                if (
                    item.get("exchange") == "NSE_FO"
                    and item.get("name", "").upper() == symbol.upper()
                    and item.get("instrument_type") == "OPTIDX"
                    and item.get("expiry")
                )
            })
            if not expiries:
                logging.error(f"❌ No expiries found for {symbol}")
                return None
            logging.info(f"📅 Nearest expiry for {symbol}: {expiries[0]}")
            return expiries[0]
        except Exception as e:
            logging.error(f"❌ get_nearest_expiry error: {e}")
            return None

    def get_option_chain(self, symbol="NIFTY", range_size=1000):
        try:
            instruments = self.load_instruments()
            spot = self.get_spot(symbol)
            if not spot:
                logging.error(f"❌ Could not fetch spot for {symbol}")
                return [], None

            expiry = self.get_nearest_expiry(symbol)
            if not expiry:
                logging.error(f"❌ No expiry found for {symbol}")
                return [], None

            logging.info(f"📅 Building option chain for {symbol} | Expiry: {expiry} | Spot: {spot}")

            options = [
                i for i in instruments
                if (
                    i.get("exchange") == "NSE_FO"
                    and i.get("name", "").upper() == symbol.upper()
                    and i.get("instrument_type") == "OPTIDX"
                    and i.get("expiry") == expiry
                )
            ]

            if not options:
                logging.error(f"❌ No options found for {symbol} expiry {expiry}")
                return [], None

            grouped = defaultdict(lambda: {"strikePrice": None, "CE": {}, "PE": {}})

            for opt in options:
                strike_raw = opt.get("strike")
                if not strike_raw:
                    continue
                try:
                    strike = float(strike_raw)
                except ValueError:
                    continue

                if abs(strike - spot) > range_size:
                    continue

                try:
                    response = self.safe_ltp([opt["instrument_key"]])
                    if not response or not response.data:
                        continue

                    ltp_obj = list(response.data.values())[0]
                    side = opt.get("option_type")
                    if side not in ("CE", "PE"):
                        continue

                    entry = grouped[strike]
                    entry["strikePrice"] = strike

                   # Fetch OI + IV from full quote, fallback to hardcoded defaults
                    oi = 0
                    iv = 0.2
                    try:
                        full = self.safe_quote([opt["instrument_key"]])
                        if full and full.data:
                            q = list(full.data.values())[0]
                            oi = getattr(q, "oi", None)
                            iv = getattr(q, "implied_volatility", None)
                    except Exception:
                        pass

                    # Hardcoded fallbacks if API returns None/0
                    if not oi or oi == 0:
                        oi = 50000  # default OI — treat all strikes equally

                    if not iv or iv == 0:
                        iv = 0.15   # 15% IV — typical for near-expiry NIFTY

                    # Tradingsymbol fallback: build it from parts if missing
                    tradingsymbol = opt.get("tradingsymbol", "").strip()
                    if not tradingsymbol:
                        expiry_fmt = expiry.replace("-", "")  # 20260428
                        tradingsymbol = f"NIFTY{expiry_fmt}{int(strike)}{side}"

                    entry[side] = {
                        "ltp":            getattr(ltp_obj, "last_price", None),
                        "instrument_key": opt["instrument_key"],
                        "tradingsymbol":  tradingsymbol,
                        "oi":             oi,
                        "iv":             iv,
                    }
                    time.sleep(0.1)

                except Exception as e:
                    logging.warning(f"⚠️ Skipping strike {strike}: {e}")
                    continue

            chain = sorted(grouped.values(), key=lambda x: x["strikePrice"])
            logging.info(f"📊 Option chain built: {len(chain)} strikes for {symbol}")
            return chain, spot

        except Exception as e:
            logging.error(f"❌ get_option_chain error: {e}")
            return [], None

    def place_order(self, instrument_key, side, qty, price=None):
        for attempt in range(3):
            try:
                order = {
                    "quantity": qty,
                    "product": "D",
                    "validity": "DAY",
                    "instrument_token": instrument_key,
                    "order_type": "MARKET" if price is None else "LIMIT",
                    "transaction_type": side.upper(),
                    "price": price or 0,
                    "tag": "algo_bot"
                }
                response = self.order_api.place_order(order)
                logging.info(f"✅ Order placed: {response}")
                return response
            except Exception as e:
                logging.warning(f"[Retry {attempt+1}] Order failed: {e}")
                time.sleep(1)
        logging.error("❌ Order failed after 3 attempts")
        return None

    def get_positions(self):
        return []

    def logout(self):
        logging.info("👋 Logout")