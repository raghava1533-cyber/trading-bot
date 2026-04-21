import asyncio
import datetime
import traceback
import logging

from data.candles import fetch_candles
from data.option_chain import get_nse_option_chain
from ml.regime_xgb import load_model, predict_regime
from greeks.engine import greeks_fd
from broker.kotak import Broker
from infra.redis_bus import set_data

INDEX = "NIFTY"

POLL_INTERVAL = 30   # safer
TRADE_COOLDOWN = 1800  # 30 min
MAX_TRADES_PER_DAY = 3

# ==============================
# STATE
# ==============================
last_trade_time = None
trade_count = 0
active_position = None


# ==============================
# UTIL
# ==============================
def log(msg):
    print(f"{datetime.datetime.now()} | {msg}")


def market_open():
    now = datetime.datetime.now().time()
    return now >= datetime.time(9, 20) and now <= datetime.time(15, 15)


def can_trade():
    global last_trade_time, trade_count

    if trade_count >= MAX_TRADES_PER_DAY:
        log("⚠️ Max trades reached")
        return False

    if last_trade_time:
        diff = (datetime.datetime.now() - last_trade_time).seconds
        if diff < TRADE_COOLDOWN:
            log(f"⏳ Cooldown active ({diff}s)")
            return False

    return True


# =========================================
# STRATEGY CORE
# =========================================
async def run_cycle(broker, model):
    global active_position, last_trade_time, trade_count


    try:
        log("🔁 Cycle start")

        if not market_open():
            log("⛔ Market closed")
            return

        # ==============================
        # FETCH DATA
        # ==============================
        candles = fetch_candles()
        if candles is None or candles.empty:
            log("❌ No candles")
            return

        regime = predict_regime(model, candles)
        log(f"📊 Regime: {regime}")
        chain, spot = get_nse_option_chain(INDEX)
        if not chain or spot == 0:
            log("❌ No option chain")
            return

        if active_position:
            log(f"📌 Active position: {active_position}")
            # simple exit condition (demo)
            if regime != active_position["regime"]:
                log("❌ Regime mismatch, closing position", level=logging.ERROR)
                active_position = None
            return

        # ==============================
        # ENTRY CONDITIONS
        # ==============================
        if not can_trade():
            log("⚠️ Cannot trade now", level=logging.ERROR)
            return

        # ==============================
        # STRIKE SELECTION
        # ==============================
        strikes = [row["strikePrice"] for row in chain if "strikePrice" in row]
        atm = min(strikes, key=lambda x: abs(x - spot))

        # ... (rest of your strategy logic here) ...

    except Exception as e:
        log(f"Exception in run_cycle: {e}", level=logging.ERROR)
        traceback.print_exc()

def setup_logging():
    logging.basicConfig(
        format='%(asctime)s | %(levelname)s | %(message)s',
        level=logging.INFO,
        handlers=[
            logging.FileHandler("trading_bot.log"),
            logging.StreamHandler()
        ]
    )



# =========================================
# MAIN
# =========================================
async def main():
    log("🚀 Bot started")

    broker = Broker()
    # broker.login("TOTP")

    model = load_model()

    while True:
        try:
            await run_cycle(broker, model)
        except Exception as e:
            log(f"❌ Trade failed: {e}", level=logging.ERROR)
            logging.exception("Exception in execute_trade")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())