

import os
from dotenv import load_dotenv
from upstox_api.api import Upstox

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

    def login(self):
        try:
            print("🔐 Logging into Kotak Neo...")
            self.client.totp_login(mobile_number=self.mobile_number, ucc=self.ucc, totp=self.totp)
            self.client.totp_validate(mpin=self.mpin)
            self.session_token = self.client.session_id
            print("✅ Login successful (LIVE)")
        except Exception as e:
            print("❌ Login failed:", e)

    def get_spot(self, symbol="NIFTY"):
        """
        Returns spot price (LIVE) using Upstox
        """
        # Example for NIFTY spot (replace with correct exchange/symbol as needed)
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
        # Placeholder: You need to implement this using Upstox API (option chain is not a direct endpoint)
        print("[DEBUG] get_option_chain: Not implemented. Use Upstox API to fetch option chain data.")
        return [], None

    # =========================================
    # ORDER EXECUTION
    # =========================================
    def place_order(self, symbol, side, qty, price=None):
        try:
            print(f"📤 ORDER -> {side} {symbol} QTY={qty} PRICE={price}")

            if self.session_token is None:
                print("❌ Not logged in")
                return None

            # 🟡 STUB ORDER
            order_id = f"SIM-{int(time.time())}"
            print(f"✅ Simulated Order ID: {order_id}")
            return order_id

            # 🔴 TODO: REAL ORDER
            """
            url = f"{self.base_url}/orders/place"

            headers = {"Authorization": self.session_token}

            payload = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "type": "MARKET"
            """

            r = requests.post(url, headers=headers, json=payload)
            data = r.json()

            return data.get("orderId")
            """

        except Exception as e:
            print("❌ Order failed:", e)
            return None

    # =========================================
    # POSITIONS (future PnL engine)
    # =========================================
    def get_positions(self):
        return []

    # =========================================
    # LOGOUT
    # =========================================
    def logout(self):
        print("👋 Logging out")
        self.session_token = None