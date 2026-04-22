def show_open_positions(engine):
    print("\nOpen Positions:")
    found = False
    for pos in engine.positions:
        if pos["open"]:
            found = True
            print(f"Strategy: {pos['strategy']}")
            for leg in pos["legs"]:
                print(f"  {leg['side']} {leg['strike']} {leg['type']} @ {leg['price']}")
    if not found:
        print("  None")
import asyncio
import datetime
import traceback
import logging
import sys

from execution.paper_engine import PaperEngine
from data.candles import fetch_candles
from ml.regime_xgb import load_model, predict_regime
from greeks.engine import greeks_fd
from broker.upstox import Broker
from infra.redis_bus import set_data

# =========================================
# CONFIG
# =========================================
INDEX = "NIFTY"

POLL_INTERVAL = 30
TRADE_COOLDOWN = 1800
MAX_TRADES_PER_DAY = 3

# =========================================
# STATE
# =========================================
last_trade_time = None
trade_count = 0

# =========================================
# LOGGING
# =========================================
def setup_logging():
    logging.basicConfig(
        format='%(asctime)s | %(levelname)s | %(message)s',
        level=logging.INFO,
        handlers=[
            logging.FileHandler("trading_bot.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )

def log(msg, level=logging.INFO):
    logging.log(level, msg)

# =========================================
# UTIL
# =========================================
def market_open():
    now = datetime.datetime.now().time()
    return now >= datetime.time(9, 20) and now <= datetime.time(15, 15)

def can_trade():
    global last_trade_time, trade_count

    if trade_count >= MAX_TRADES_PER_DAY:
        log("Max trades reached", logging.WARNING)
        return False

    if last_trade_time:
        diff = (datetime.datetime.now() - last_trade_time).seconds
        if diff < TRADE_COOLDOWN:
            log(f"Cooldown active ({diff}s)", logging.WARNING)
            return False

    return True

# =========================================
# STRIKE + PRICE HELPERS
# =========================================
def get_atm_strike(chain, spot):
    strikes = [row["strikePrice"] for row in chain]
    return min(strikes, key=lambda x: abs(x - spot))

def get_ltp(chain, strike, option_type):
    for row in chain:
        if row["strikePrice"] == strike:
            return row[option_type]["ltp"]
    return None

# =========================================
# BUILD STRATEGIES (REAL MULTI-LEG)
# =========================================
def build_iron_condor(chain, spot):
    atm = get_atm_strike(chain, spot)

    ce_sell = atm + 100
    ce_buy  = atm + 200
    pe_sell = atm - 100
    pe_buy  = atm - 200

    return [
        ("SELL", ce_sell, "CE"),
        ("BUY",  ce_buy,  "CE"),
        ("SELL", pe_sell, "PE"),
        ("BUY",  pe_buy,  "PE"),
    ]

def build_bull_put(chain, spot):
    atm = get_atm_strike(chain, spot)

    pe_sell = atm - 50
    pe_buy  = atm - 150

    return [
        ("SELL", pe_sell, "PE"),
        ("BUY",  pe_buy,  "PE"),
    ]

def build_bear_call(chain, spot):
    atm = get_atm_strike(chain, spot)

    ce_sell = atm + 50
    ce_buy  = atm + 150

    return [
        ("SELL", ce_sell, "CE"),
        ("BUY",  ce_buy,  "CE"),
    ]

# =========================================
# EXECUTE TRADE (PAPER ENGINE)
# =========================================
async def execute_trade(engine, strategy_name, legs, chain):
    global last_trade_time, trade_count

    try:
        log(f"Executing {strategy_name}")

        trade_legs = []

        for side, strike, opt_type in legs:
            ltp = get_ltp(chain, strike, opt_type)

            if ltp is None:
                log(f"Missing LTP for {strike} {opt_type}", logging.ERROR)
                return

            trade_legs.append({
                "side": side,
                "strike": strike,
                "type": opt_type,
                "price": ltp
            })

        engine.add_position(strategy_name, trade_legs)

        last_trade_time = datetime.datetime.now()
        trade_count += 1

        log(f"Trade executed: {strategy_name}")

    except Exception:
        log("Trade execution failed", logging.ERROR)
        traceback.print_exc()

# =========================================
# MAIN STRATEGY LOOP
# =========================================
async def run_cycle(broker, model, engine):
    try:
        log("Cycle start")

        if not market_open():
            log("Market closed")
            return

        # ==============================
        # FETCH DATA
        # ==============================
        candles = fetch_candles()

        if candles is None or candles.empty:
            log("No candles")
            return

        regime = predict_regime(model, candles)
        log(f"Regime: {regime}")

        # ==============================
        # OPTION CHAIN
        # ==============================
        chain, spot = broker.get_option_chain(INDEX)

        if not chain:
            log("No option chain")
            return

        log(f"Spot: {spot}")

        # ==============================
        # UPDATE PnL (REALISTIC MTM)
        # ==============================
        engine.mark_to_market(chain)

        pnl = engine.get_pnl()
        log(f"MTM PnL: {pnl['total']}")
        log(f"PnL Summary: {pnl}")

        # Redis/dashboard
        set_data("pnl", str(pnl))
        set_data("spot", spot)
        set_data("regime", regime)

        # ==============================
        # GREEKS
        # ==============================
        atm = get_atm_strike(chain, spot)
        greeks = greeks_fd(spot, atm + 50, atm - 50)

        log(f"Greeks: {greeks}")
        set_data("greeks", str(greeks))

        # ==============================
        # ENTRY
        # ==============================
        if engine.has_open_positions():
            return

        if not can_trade():
            return

        if regime == "SIDE":
            legs = build_iron_condor(chain, spot)
            await execute_trade(engine, "IRON_CONDOR", legs, chain)

        elif regime == "BULL":
            legs = build_bull_put(chain, spot)
            await execute_trade(engine, "BULL_PUT", legs, chain)

        elif regime == "BEAR":
            legs = build_bear_call(chain, spot)
            await execute_trade(engine, "BEAR_CALL", legs, chain)

        log("Cycle complete\n")

    except Exception:
        log("Error in cycle", logging.ERROR)
        traceback.print_exc()

# =========================================
# MAIN
# =========================================
async def main():
    setup_logging()
    log("Bot started")

    broker = Broker()
    engine = PaperEngine()
    model = load_model()

    while True:
        try:
            await run_cycle(broker, model, engine)
        except Exception as e:
            log(f"Loop error: {e}", logging.ERROR)
            traceback.print_exc()

        await asyncio.sleep(POLL_INTERVAL)

# =========================================
# ENTRY
# =========================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot stopped")