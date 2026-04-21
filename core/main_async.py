import asyncio
import datetime
import traceback

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

        log(f"✅ Candles: {len(candles)}")

        regime = predict_regime(model, candles)
        log(f"📊 Regime: {regime}")

        chain, spot = get_nse_option_chain(INDEX)
        if not chain or spot == 0:
            log("❌ No option chain")
            return

        log(f"📈 Spot: {spot}")

        # ==============================
        # POSITION MONITOR
        # ==============================
        if active_position:
            log(f"📌 Active position: {active_position}")

            # simple exit condition (demo)
            if regime != active_position["regime"]:
                log("🔄 Regime changed → exit position")
                active_position = None

            return

        # ==============================
        # ENTRY CONDITIONS
        # ==============================
        if not can_trade():
            return

        # ==============================
        # STRIKE SELECTION
        # ==============================
        strikes = [row["strikePrice"] for row in chain if "strikePrice" in row]
        atm = min(strikes, key=lambda x: abs(x - spot))

        ce = atm + 50
        pe = atm - 50

        log(f"🎯 CE={ce}, PE={pe}")

        # ==============================
        # GREEKS
        # ==============================
        greeks = greeks_fd(spot, ce, pe)
        log(f"🧮 {greeks}")

        set_data("spot", spot)
        set_data("greeks", str(greeks))
        set_data("regime", regime)

        # ==============================
        # EXECUTE TRADE
        # ==============================
        await execute_trade(broker, regime, ce, pe)

        # update state
        active_position = {
            "regime": regime,
            "ce": ce,
            "pe": pe,
            "entry_time": datetime.datetime.now()
        }

        last_trade_time = datetime.datetime.now()
        trade_count += 1

        log(f"📊 Trades today: {trade_count}")

    except Exception:
        log("🚨 ERROR")
        traceback.print_exc()


# =========================================
# EXECUTION
# =========================================
async def execute_trade(broker, regime, ce, pe):
    try:
        log("🚀 Executing trade")

        if regime == "SIDE":
            log(f"IRON CONDOR → CE {ce} | PE {pe}")

        elif regime == "BULL":
            log(f"BULL PUT → PE {pe}")

        elif regime == "BEAR":
            log(f"BEAR CALL → CE {ce}")

        await asyncio.sleep(1)

        log("✅ Trade executed (paper mode)")

    except Exception:
        log("❌ Trade failed")
        traceback.print_exc()


# =========================================
# MAIN
# =========================================
async def main():
    log("🚀 Bot started")

    broker = Broker()
    # broker.login("TOTP")

    model = load_model()

    while True:
        log("🔄 Loop running...")
        await run_cycle(broker, model)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())