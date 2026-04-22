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

    # ---------------------------------------------------
    # SAFE LTP (SDK COMPATIBLE)
    # ---------------------------------------------------
    def safe_ltp(self, keys):
        try:
            return self.market_api.ltp(symbol=keys, api_version="v2")
        except TypeError:
            return self.market_api.ltp(symbol=keys)

    # ---------------------------------------------------
    # LOAD INSTRUMENT MASTER (ROBUST + CACHE)
    # ---------------------------------------------------
    def load_instruments(self):
        # 1. Try memory cache
        if self.instrument_cache:
            return self.instrument_cache

        # 2. Try local file cache
        if os.path.exists(self.instrument_file):
            try:
                with open(self.instrument_file, "r") as f:
                    data = json.load(f)
                    self.instrument_cache = data
                    logging.info(f"✅ Loaded instruments from file ({len(data)})")
                    return data
            except Exception:
                logging.warning("⚠️ Failed to read local cache, re-downloading...")

        # 3. Download from Upstox (correct URL + gzip CSV)
        logging.info("📥 Downloading instrument master...")

        url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"

        for attempt in range(3):
            try:
                response = requests.get(url, timeout=15)

                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")

                if not response.content:
                    raise Exception("Empty response")

                # Decompress gzip
                compressed = io.BytesIO(response.content)
                with gzip.GzipFile(fileobj=compressed) as f:
                    content = f.read().decode("utf-8")

                # Parse CSV into list of dicts
                reader = csv.DictReader(io.StringIO(content))
                data = [row for row in reader]

                if not data:
                    raise Exception("Empty CSV after parsing")

                # Save to local cache
                with open(self.instrument_file, "w") as f:
                    json.dump(data, f)

                self.instrument_cache = data
                logging.info(f"✅ Instruments loaded: {len(data)}")
                return data

            except Exception as e:
                logging.warning(f"[Retry {attempt+1}] Instrument load failed: {e}")
                time.sleep(1)

        raise Exception("❌ Failed to load instrument master")

    # ---------------------------------------------------
    # GET SPOT PRICE
    # ---------------------------------------------------
    def get_spot(self, symbol="NIFTY", retries=3):
        try:
            instruments = self.load_instruments()

            instrument_key = None
            for item in instruments:
                if (
                    item.get("segment") == "NSE_INDEX"
                    and symbol.upper() in item.get("name", "").upper()
                ):
                    instrument_key = item.get("instrument_key")
                    break

            if not instrument_key:
                raise Exception(f"{symbol} index not found in instruments")

            for attempt in range(retries):
                try:
                    response = self.safe_ltp([instrument_key])

                    if response.data:
                        ltp_obj = list(response.data.values())[0]
                        price = float(ltp_obj.last_price)
                        logging.info(f"📊 {symbol} Spot = {price}")
                        return price

                except Exception as e:
                    logging.warning(f"[Retry {attempt+1}] get_spot failed: {e}")
                    time.sleep(0.5)

            return None

        except Exception as e:
            logging.error(f"❌ get_spot error: {e}")
            return None

    # ---------------------------------------------------
    # GET NEAREST EXPIRY
    # ---------------------------------------------------
    def get_nearest_expiry(self, symbol="NIFTY"):
        instruments = self.load_instruments()

        expiries = sorted({
            item.get("expiry")
            for item in instruments
            if (
                item.get("segment") == "NSE_FO"
                and symbol.upper() in item.get("name", "").upper()
                and item.get("expiry")
            )
        })

        return expiries[0] if expiries else None

    # ---------------------------------------------------
    # OPTION CHAIN (GROUPED)
    # ---------------------------------------------------
    def get_option_chain(self, symbol="NIFTY", range_size=1000):
        try:
            instruments = self.load_instruments()
            spot = self.get_spot(symbol)

            if not spot:
                return [], None

            expiry = self.get_nearest_expiry(symbol)
            if not expiry:
                logging.error(f"❌ No expiry found for {symbol}")
                return [], None

            logging.info(f"📅 Using expiry: {expiry}")

            options = [
                i for i in instruments
                if (
                    i.get("segment") == "NSE_FO"
                    and symbol.upper() in i.get("name", "").upper()
                    and i.get("expiry") == expiry
                )
            ]

            if not options:
                logging.error(f"❌ No options found for {symbol} expiry {expiry}")
                return [], None

            grouped = defaultdict(lambda: {"strikePrice": None, "CE": {}, "PE": {}})

            for opt in options:
                # CSV uses "strike" not "strike_price" — handle both
                strike = opt.get("strike") or opt.get("strike_price")
                if not strike:
                    continue

                try:
                    strike = float(strike)
                except ValueError:
                    continue

                if abs(strike - spot) > range_size:
                    continue

                try:
                    response = self.safe_ltp([opt["instrument_key"]])
                    data = response.data
                    ltp_obj = list(data.values())[0]

                    entry = grouped[strike]
                    entry["strikePrice"] = strike

                    side = opt.get("instrument_type")  # CE or PE

                    entry[side] = {
                        "ltp": getattr(ltp_obj, "last_price", None),
                        "instrument_key": opt["instrument_key"]
                    }

                    time.sleep(0.03)

                except Exception:
                    continue

            chain = list(grouped.values())

            logging.info(f"📊 Option chain built: {len(chain)} strikes")
            return chain, spot

        except Exception as e:
            logging.error(f"❌ option chain error: {e}")
            return [], None

    # ---------------------------------------------------
    # PLACE ORDER
    # ---------------------------------------------------
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
                logging.warning(f"[Retry {attempt+1}] order failed: {e}")
                time.sleep(1)

        logging.error("❌ Order failed after 3 attempts")
        return None

    # ---------------------------------------------------
    def get_positions(self):
        return []

    def logout(self):
        logging.info("👋 Logout")