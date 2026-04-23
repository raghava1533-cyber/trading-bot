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
from strategy.strike_selector import select_strikes

# =========================================
# CONFIG
# =========================================
INDEX              = "NIFTY"
LOT_SIZE           = 75
POLL_INTERVAL      = 30
TRADE_COOLDOWN     = 1800
MAX_TRADES_PER_DAY = 3
STOP_LOSS          = -2000   # ₹ unrealized loss  → exit
TARGET             =  3000   # ₹ unrealized profit → exit
TARGET_DELTA       =  0.25   # ~25-delta strike selection

# =========================================
# STATE
# =========================================
last_trade_time = None
trade_count     = 0

# =========================================
# LOGGING
# =========================================
def setup_logging():
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s",
        level=logging.INFO,
        handlers=[
            logging.FileHandler("trading_bot.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
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
        return False
    if last_trade_time:
        diff = (datetime.datetime.now() - last_trade_time).seconds
        if diff < TRADE_COOLDOWN:
            return False
    return True

def _cooldown_str():
    global last_trade_time
    if not last_trade_time:
        return "Ready"
    diff      = (datetime.datetime.now() - last_trade_time).seconds
    remaining = max(0, TRADE_COOLDOWN - diff)
    if remaining == 0:
        return "Ready"
    m, s = divmod(remaining, 60)
    return f"{m}m{s:02d}s"

def get_ltp(chain, strike, option_type):
    for row in chain:
        if row["strikePrice"] == strike:
            return row[option_type].get("ltp")
    return None

def get_atm_strike(chain, spot):
    strikes = [row["strikePrice"] for row in chain]
    return min(strikes, key=lambda x: abs(x - spot))

# =========================================
# TERMINAL DASHBOARD
# =========================================
def render_dashboard(spot, regime, pnl, greeks, positions, margin_info=None):
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
    DIM    = "\033[2m"

    W    = 96
    sep  = "─" * W
    dsep = "═" * W

    now  = datetime.datetime.now().strftime("%H:%M:%S")
    date = datetime.datetime.now().strftime("%d-%b-%Y")

    def center(text):
        clean = ""
        skip  = False
        for ch in text:
            if ch == "\033":
                skip = True
            if not skip:
                clean += ch
            if skip and ch == "m":
                skip = False
        pad = max(0, W - len(clean))
        return " " * (pad // 2) + text + " " * (pad - pad // 2)

    pnl_color = GREEN if pnl["total"] >= 0 else RED
    reg_color = GREEN if regime == "BULL" else (RED if regime == "BEAR" else YELLOW)
    arrow     = "▲" if pnl["total"] >= 0 else "▼"

    lines = []
    lines.append(dsep)
    lines.append(center(f"{BOLD}{CYAN}  NIFTY ALGO BOT  {RESET}{DIM}│ {date} {now}{RESET}"))
    lines.append(dsep)

    # ── Status ──
    lines.append(
        f"  {BOLD}SPOT{RESET}  {CYAN}{spot:>10,.2f}{RESET}"
        f"   {BOLD}REGIME{RESET}  {reg_color}{regime:<6}{RESET}"
        f"   {BOLD}TRADES{RESET}  {trade_count}/{MAX_TRADES_PER_DAY}"
        f"   {BOLD}COOLDOWN{RESET}  {_cooldown_str()}"
        f"   {BOLD}POSITIONS{RESET}  {pnl['open_positions']}"
    )
    lines.append(sep)

    # ── PnL ──
    lines.append(
        f"  {BOLD}REALIZED{RESET}   {pnl_color}₹{pnl['realized']:>+10,.2f}{RESET}"
        f"   {BOLD}UNREALIZED{RESET}  {pnl_color}₹{pnl['unrealized']:>+10,.2f}{RESET}"
        f"   {BOLD}TOTAL{RESET}  {pnl_color}{arrow} ₹{pnl['total']:>+10,.2f}{RESET}"
    )
    lines.append(sep)

    # ── Greeks ──
    cd = greeks.get("call_delta", 0)
    pd = greeks.get("put_delta",  0)
    lines.append(
        f"  {BOLD}CALL Δ{RESET}  {cd:>+.4f}"
        f"   {BOLD}PUT Δ{RESET}  {pd:>+.4f}"
        f"   {BOLD}SL{RESET}  ₹{STOP_LOSS:,}"
        f"   {BOLD}TARGET{RESET}  ₹{TARGET:,}"
        f"   {BOLD}Δ-TARGET{RESET}  {TARGET_DELTA}"
    )
    lines.append(sep)

    # ── Margin info (shown when a position exists) ──
    if margin_info:
        mp_color = GREEN
        ml_color = RED
        lines.append(
            f"  {BOLD}MARGIN REQUIRED{RESET}  ₹{margin_info['margin_required']:>10,.2f}"
            f"   {BOLD}NET CREDIT{RESET}  ₹{margin_info['net_credit']:>+6,.2f}/lot"
            f"   {mp_color}{BOLD}MAX PROFIT{RESET}  ₹{margin_info['max_profit']:>8,.2f}{RESET}"
            f"   {ml_color}{BOLD}MAX LOSS{RESET}  ₹{margin_info['max_loss']:>8,.2f}{RESET}"
        )
        lines.append(sep)

    # ── Positions table ──
    hdr = f"{'STRATEGY':<16}{'SIDE':<6}{'SYMBOL':<24}{'TYPE':<5}{'STRIKE':>8}{'ENTRY':>8}{'LTP':>8}{'PnL':>10}"
    lines.append(f"  {BOLD}{hdr}{RESET}")
    lines.append(sep)

    open_pos = [p for p in positions if p["open"]]
    if not open_pos:
        lines.append(center(f"{DIM}── No open positions ──{RESET}"))
    else:
        for pos in open_pos:
            for leg in pos["legs"]:
                leg_pnl  = leg.get("unrealized_pnl", 0)
                lp_color = GREEN if leg_pnl >= 0 else RED
                symbol   = leg.get("symbol", "—")
                ltp_disp = f"{leg['ltp']:,.2f}" if leg.get("ltp") else "—"
                row = (
                    f"  {pos['strategy']:<16}"
                    f"{leg['side']:<6}"
                    f"{symbol:<24}"
                    f"{leg['type']:<5}"
                    f"{leg['strike']:>8,.0f}"
                    f"{leg['price']:>8,.2f}"
                    f"{ltp_disp:>8}"
                    f"{lp_color}{leg_pnl:>+10,.2f}{RESET}"
                )
                lines.append(row)

    lines.append(dsep)

    sys.stdout.write("\033[H\033[J")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


# =========================================
# UPDATE LEG LTP FOR DASHBOARD
# =========================================
def _update_leg_ltp(engine, chain):
    for pos in engine.positions:
        if not pos["open"]:
            continue
        for leg in pos["legs"]:
            ltp = get_ltp(chain, leg["strike"], leg["type"])
            if ltp is not None:
                leg["ltp"] = ltp
                entry = leg["price"]
                leg["unrealized_pnl"] = (ltp - entry) if leg["side"] == "BUY" else (entry - ltp)


# =========================================
# EXECUTE TRADE
# =========================================
async def execute_trade(engine, strategy_name, legs, chain):
    """
    legs format: (side, strike, opt_type, symbol, ltp, margin_info)
    """
    global last_trade_time, trade_count
    try:
        log(f"Executing {strategy_name}")
        trade_legs  = []
        margin_info = None

        for leg in legs:
            side, strike, opt_type, symbol, entry_ltp, margin = leg

            # Use entry_ltp from selector; fallback to live chain
            ltp = entry_ltp or get_ltp(chain, strike, opt_type)
            if ltp is None:
                log(f"Missing LTP for {strike} {opt_type}", logging.ERROR)
                return

            margin_info = margin   # same for all legs in a spread

            trade_legs.append({
                "side":          side,
                "strike":        strike,
                "type":          opt_type,
                "price":         ltp,
                "symbol":        symbol,
                "ltp":           ltp,
                "unrealized_pnl": 0,
            })

        engine.add_position(strategy_name, trade_legs, margin_info)
        last_trade_time = datetime.datetime.now()
        trade_count    += 1
        log(f"Trade executed: {strategy_name}")

    except Exception:
        log("Trade execution failed", logging.ERROR)
        traceback.print_exc()


# =========================================
# MAIN CYCLE
# =========================================
async def run_cycle(broker, model, engine):
    try:
        if not market_open():
            return

        candles = fetch_candles()
        if candles is None or candles.empty:
            return

        regime = predict_regime(model, candles)

        chain, spot = broker.get_option_chain(INDEX)
        if not chain:
            return

        # MTM + leg LTP update
        engine.mark_to_market(chain)
        _update_leg_ltp(engine, chain)

        pnl         = engine.get_pnl()
        margin_info = engine.get_margin_info()

        set_data("pnl",    str(pnl))
        set_data("spot",   spot)
        set_data("regime", regime)

        atm    = get_atm_strike(chain, spot)
        greeks = greeks_fd(spot, atm + 50, atm - 50)
        set_data("greeks", str(greeks))

        render_dashboard(spot, regime, pnl, greeks, engine.positions, margin_info)

        # ── EXIT: stop-loss / target ──
        if pnl["unrealized"] <= STOP_LOSS:
            log(f"🛑 Stop loss hit: ₹{pnl['unrealized']:,.2f}", logging.WARNING)
            engine.close_all()
            return

        if pnl["unrealized"] >= TARGET:
            log(f"✅ Target hit: ₹{pnl['unrealized']:,.2f}")
            engine.close_all()
            return

        # ── ENTRY: only if NO open positions ──
        # has_open_positions() blocks new trades until current one is closed
        if engine.has_open_positions():
            return

        if not can_trade():
            return

        if regime == "SIDE":
            legs = select_strikes(chain, spot, "IRON_CONDOR", target_delta=TARGET_DELTA, lot_size=LOT_SIZE)
            if legs:
                await execute_trade(engine, "IRON_CONDOR", legs, chain)

        elif regime == "BULL":
            legs = select_strikes(chain, spot, "BULL_PUT", target_delta=TARGET_DELTA, lot_size=LOT_SIZE)
            if legs:
                await execute_trade(engine, "BULL_PUT", legs, chain)

        elif regime == "BEAR":
            legs = select_strikes(chain, spot, "BEAR_CALL", target_delta=TARGET_DELTA, lot_size=LOT_SIZE)
            if legs:
                await execute_trade(engine, "BEAR_CALL", legs, chain)

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
    model  = load_model()

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    while True:
        try:
            await run_cycle(broker, model, engine)
        except Exception as e:
            log(f"Loop error: {e}", logging.ERROR)
            traceback.print_exc()
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot stopped")