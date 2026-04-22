# Upstox Broker integration
import os
from dotenv import load_dotenv
import upstox_client
from upstox_client import Configuration, ApiClient
from upstox_client.api.market_quote_api import MarketQuoteApi
from upstox_client.api.options_api import OptionsApi
from upstox_client.api.instruments_api import InstrumentsApi

# Load .env if present
load_dotenv()



class Broker:
	def __init__(self):
		print("✅ Upstox Broker initializing...")

		# ENV CONFIG (fallback to input)
		self.api_key = os.getenv("UPSTOX_API_KEY") or input("Enter Upstox API Key: ").strip()
		self.api_secret = os.getenv("UPSTOX_API_SECRET") or input("Enter Upstox API Secret: ").strip()
		self.redirect_uri = os.getenv("UPSTOX_REDIRECT_URI") or input("Enter Upstox Redirect URI: ").strip()
		self.access_token = os.getenv("UPSTOX_ACCESS_TOKEN") or input("Enter Upstox Access Token: ").strip()

		# Upstox API v2 SDK client setup
		config = Configuration()
		config.access_token = self.access_token
		self.api_client = ApiClient(configuration=config)
		self.market_api = MarketQuoteApi(self.api_client)
		self.options_api = OptionsApi(self.api_client)
		self.instruments_api = InstrumentsApi(self.api_client)



	def login(self):
		print("[INFO] Upstox login is handled via access token. Use Upstox web flow to generate and set it in .env.")



	def get_spot(self, symbol="NIFTY"):
		"""
		Returns spot price (LIVE) using Upstox API v2
		"""
		try:
			# Try all possible instrument key formats for spot price
			instruments = [
				f"NSE_INDEX|{symbol.upper()}",
				f"NSE_FO|{symbol.upper()}",
				f"NSE_EQ|{symbol.upper()}"
			]
			for instrument in instruments:
				print(f"[DEBUG] Trying instrument: {instrument}")
				try:
					response = self.market_api.ltp(symbol=instrument, api_version="v2")
					if hasattr(response, 'data') and response.data:
						print(f"[DEBUG] get_spot: response.data = {response.data}")
						if isinstance(response.data, dict):
							ltp_obj = list(response.data.values())[0]
							ltp = getattr(ltp_obj, 'last_price', None)
						elif isinstance(response.data, list):
							ltp_obj = response.data[0]
							ltp = getattr(ltp_obj, 'last_price', None)
						print(f"[DEBUG] get_spot: {ltp}")
						if ltp is not None:
							return float(ltp)
				except Exception as e:
					print(f"[DEBUG] get_spot: instrument {instrument} failed: {e}")
			print(f"[ERROR] get_spot: All instrument keys failed for {symbol}")
			return None
		except Exception as e:
			print(f"[ERROR] get_spot: {e}")
			return None


	def get_option_chain(self, symbol="NIFTY"):
		"""
		Returns option chain (LIVE) using Upstox API v2
		"""
		try:
			# Dynamically fetch the correct instrument_key for the symbol
			search_resp = self.instruments_api.search_instrument(query=symbol.upper(), exchanges="NSE")
			instrument_key = None
			if hasattr(search_resp, 'data') and search_resp.data:
				# Find the first matching instrument_key
				for item in search_resp.data:
					if item.get('symbol') == symbol.upper():
						instrument_key = item.get('instrument_key')
						break
				if not instrument_key:
					instrument_key = search_resp.data[0].get('instrument_key')
			if not instrument_key:
				print(f"[ERROR] get_option_chain: Could not find instrument_key for {symbol}")
				return [], None
			response = self.options_api.get_option_contracts(instrument_key)
			raw_chain = response.data if hasattr(response, 'data') else []
			print(f"[DEBUG] get_option_chain: {len(raw_chain)} strikes fetched.")
			# Transform to expected structure
			chain = []
			spot = None
			for row in raw_chain:
				strike = getattr(row, 'strike_price', None)
				# Only set spot if it's a valid float
				row_spot = getattr(row, 'underlying_spot_price', None)
				if spot is None and isinstance(row_spot, (float, int)) and row_spot > 0:
					spot = float(row_spot)
				ce = getattr(row, 'call_options', None)
				pe = getattr(row, 'put_options', None)
				chain.append({
					"strikePrice": strike,
					"CE": {
						"ltp": getattr(ce, 'last_traded_price', None) if ce else None,
						"oi": getattr(ce, 'open_interest', None) if ce else None,
						"iv": getattr(ce, 'implied_volatility', None) if ce else None,
					},
					"PE": {
						"ltp": getattr(pe, 'last_traded_price', None) if pe else None,
						"oi": getattr(pe, 'open_interest', None) if pe else None,
						"iv": getattr(pe, 'implied_volatility', None) if pe else None,
					}
				})
			# Fallback: If spot is still None, try to get from get_spot
			if spot is None:
				spot = self.get_spot(symbol)
			return chain, spot
		except Exception as e:
			print(f"[ERROR] get_option_chain: {e}")
			return [], None


	def place_order(self, symbol, side, qty, price=None):
		try:
			print(f"📤 ORDER -> {side} {symbol} QTY={qty} PRICE={price}")
			# TODO: Implement real Upstox order placement using API v2
			# See Upstox API docs for order parameters
			# Example stub:
			order_id = f"SIM-{symbol}-{side}-{qty}"
			print(f"✅ Simulated Order ID: {order_id}")
			return order_id
		except Exception as e:
			print("❌ Order failed:", e)
			return None


	def get_positions(self):
		# TODO: Implement using self.client.get_positions() if available
		return []

	def logout(self):
		print("👋 Logging out")
# This file marks the broker module for Upstox integration.
