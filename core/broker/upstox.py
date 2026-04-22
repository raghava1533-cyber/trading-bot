# Upstox Broker integration
import os
from dotenv import load_dotenv
import upstox_client
from upstox_client import Configuration, ApiClient
from upstox_client.api.market_quote_api import MarketQuoteApi
from upstox_client.api.options_api import OptionsApi

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



	def login(self):
		print("[INFO] Upstox login is handled via access token. Use Upstox web flow to generate and set it in .env.")



	def get_spot(self, symbol="NIFTY"):
		"""
		Returns spot price (LIVE) using Upstox API v2
		"""
		try:
			# Example for NSE_INDEX. Adjust as needed for your use case.
			response = self.market_api.get_market_quote_last_traded_price(
				exchange='NSE_INDEX', symbol=symbol)
			ltp = response.data.last_price
			print(f"[DEBUG] get_spot: {ltp}")
			return float(ltp)
		except Exception as e:
			print(f"[ERROR] get_spot: {e}")
			return None


	def get_option_chain(self, symbol="NIFTY"):
		"""
		Returns option chain (LIVE) using Upstox API v2
		"""
		try:
			# You need the instrument_key for the underlying symbol (e.g., NIFTY)
			# This is usually in the format 'NSE_INDEX|NIFTY' or 'NSE_FO|NIFTY'
			instrument_key = f"NSE_INDEX|{symbol.upper()}"
			response = self.options_api.get_option_contracts(instrument_key)
			chain = response.data if hasattr(response, 'data') else []
			print(f"[DEBUG] get_option_chain: {len(chain)} strikes fetched.")
			return chain, None
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
