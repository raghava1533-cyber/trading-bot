# Upstox Broker integration
import os
from dotenv import load_dotenv
from upstox_python_sdk import Upstox

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

		# SDK CLIENT
		self.client = Upstox(self.api_key, self.api_secret)
		self.client.set_redirect_uri(self.redirect_uri)
		self.client.set_access_token(self.access_token)

	def login(self):
		print("[INFO] Upstox login is handled via access token. Use Upstox web flow to generate and set it in .env.")

	def get_spot(self, symbol="NIFTY"):
		"""
		Returns spot price (LIVE) using Upstox
		"""
		try:
			quote = self.client.get_live_feed(symbol, 'NSE_INDEX', LiveFeedType.LTP)
			ltp = quote['ltp']
			print(f"[DEBUG] get_spot: {ltp}")
			return float(ltp)
		except Exception as e:
			print(f"[ERROR] get_spot: {e}")
			return None

	def get_option_chain(self, symbol="NIFTY"):
		"""
		Returns option chain (LIVE) using Upstox
		"""
		print("[DEBUG] get_option_chain: Not implemented. Use Upstox API to fetch option chain data.")
		return [], None

	def place_order(self, symbol, side, qty, price=None):
		try:
			print(f"📤 ORDER -> {side} {symbol} QTY={qty} PRICE={price}")
			# TODO: Implement real Upstox order placement
			order_id = f"SIM-{symbol}-{side}-{qty}"
			print(f"✅ Simulated Order ID: {order_id}")
			return order_id
		except Exception as e:
			print("❌ Order failed:", e)
			return None

	def get_positions(self):
		return []

	def logout(self):
		print("👋 Logging out")
# This file marks the broker module for Upstox integration