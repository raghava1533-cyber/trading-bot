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
# INDEX CONFIG — lot sizes + yfinance tickers
# =========================================
INDEX_CONFIG = {
    "NIFTY": {
        "lot_size":      75,
        "yf_ticker":     "^NSEI",
        "label":         "NIFTY 50",
        "range_size":    1000,
    },
    "BANKNIFTY": {
        "lot_size":      30,
        "yf_ticker":     "^NSEBANK",
        "label":         "BANK NIFTY",
        "range_size":    2000,
    },
    "SENSEX": {
        "lot_size":      10,
        "yf_ticker":     "^BSESN",
        "label":         "SENSEX",
        "range_size":    2000,
    },
}

# =========================================
# CONFIG
# =========================================
POLL_INTERVAL      = 60
TRADE_COOLDOWN     = 1800
MAX_TRADES_PER_DAY = 3
STOP_LOSS          = -2000
TARGET             =  3000
TARGET_DELTA       =  0.25

# =========================================
# STATE (per index)
# =========================================
last_trade_time = {}   # { "NIFTY": datetime, ... }
trade_count     = {}   # { "NIFTY": 0, ... }

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
# STARTUP: ask user which index to trade
# =========================================
def ask_index_selection():
    """
    Interactive prompt at startup.
    Returns list of selected index keys e.g. ["NIFTY"] or ["NIFTY","BANKNIFTY","SENSEX"]
    """
    print("\n" + "═" * 60)
    print("       NIFTY ALGO BOT — Index Selection")
    print("═" * 60)
    print("  1)  NIFTY 50        (Lot: 75)")
    print("  2)  BANK NIFTY      (Lot: 30)")
    print("  3)  SENSEX          (Lot: 10)")
    print("  4)  AUTO — scan all and pick best opportunity")
    print("═" * 60)

    while True:
        try:
            choice = input("  Enter choice (1/2/3/4): ").strip()
            if choice == "1":
                return ["NIFTY"]
            elif choice == "2":
                return ["BANKNIFTY"]
            elif choice == "3":
                return ["SENSEX"]
            elif choice == "4":
                return list(INDEX_CONFIG.keys())   # all three
            else:
                print("  ❌ Invalid choice. Enter 1, 2, 3, or 4.")
        except (KeyboardInterrupt, EOFError):
            print("\nBot cancelled.")
            sys.exit(0)


def pick_best_index(broker, model, indices):
    """
    Called when user picks option 4.
    Scores each index by: regime confidence + IV + OI liquidity.
    Returns the single best index key.
    """
    print("\n  🔍 Scanning all indices for best opportunity...\n")

    scores = {}

    for idx in indices:
        try:
            cfg  = INDEX_CONFIG[idx]

            # Fetch candles for this index
            from data.candles import fetch_candles
            candles = fetch_candles(ticker=cfg["yf_ticker"])
            if candles is None or candles.empty:
                print(f"  ⚠️  {cfg['label']}: no candle data, skipping")
                continue

            regime = predict_regime(model, candles)

            chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
            if not chain or not spot:
                print(f"  ⚠️  {cfg['label']}: no option chain, skipping")
                continue

            # Score = average total OI across ATM ±3 strikes (liquidity proxy)
            atm       = min(chain, key=lambda x: abs(x["strikePrice"] - spot))
            atm_price = atm["strikePrice"]
            nearby    = [r for r in chain if abs(r["strikePrice"] - atm_price) <= 300]
            avg_oi    = sum(
                (r.get("CE", {}).get("oi", 50000) + r.get("PE", {}).get("oi", 50000))
                for r in nearby
            ) / max(len(nearby), 1)

            # Regime score: SIDE > BULL/BEAR for premium selling
            regime_score = {"SIDE": 3, "BULL": 2, "BEAR": 2}.get(regime, 1)

            total_score = regime_score * (avg_oi / 100000)

            scores[idx] = {
                "score":   total_score,
                "regime":  regime,
                "spot":    spot,
                "avg_oi":  avg_oi,
                "label":   cfg["label"],
            }

            print(
                f"  ✅  {cfg['label']:<14}  "
                f"Spot: {spot:>10,.2f}  "
                f"Regime: {regime:<5}  "
                f"Avg OI: {avg_oi:>10,.0f}  "
                f"Score: {total_score:.2f}"
            )

        except Exception as e:
            print(f"  ❌  {idx}: error — {e}")

    if not scores:
        print("\n  ⚠️  Could not score any index. Defaulting to NIFTY.")
        return "NIFTY"

    best = max(scores, key=lambda x: scores[x]["score"])
    print(f"\n  🏆 Best index: {scores[best]['label']}  (score {scores[best]['score']:.2f})\n")
    return best


# =========================================
# UTIL
# =========================================
def market_open():
    now = datetime.datetime.now().time()
    return now >= datetime.time(9, 20) and now <= datetime.time(15, 15)

def can_trade(idx):
    if trade_count.get(idx, 0) >= MAX_TRADES_PER_DAY:
        return False
    lt = last_trade_time.get(idx)
    if lt:
        diff = (datetime.datetime.now() - lt).seconds
        if diff < TRADE_COOLDOWN:
            return False
    return True

def _cooldown_str(idx):
    lt = last_trade_time.get(idx)
    if not lt:
        return "Ready"
    diff      = (datetime.datetime.now() - lt).seconds
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
    return min([row["strikePrice"] for row in chain], key=lambda x: abs(x - spot))

# =========================================
# DASHBOARD
# =========================================
def render_dashboard(active_index, spot, regime, pnl, greeks, positions, margin_info=None):
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
    DIM    = "\033[2m"

    W    = 100
    sep  = "─" * W
    dsep = "═" * W

    now  = datetime.datetime.now().strftime("%H:%M:%S")
    date = datetime.datetime.now().strftime("%d-%b-%Y")
    cfg  = INDEX_CONFIG.get(active_index, {})

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
    label     = cfg.get("label", active_index)

    lines = []
    lines.append(dsep)
    lines.append(center(
        f"{BOLD}{CYAN}  {label} ALGO BOT  {RESET}"
        f"{DIM}│ {date} {now}{RESET}"
    ))
    lines.append(dsep)

    lines.append(
        f"  {BOLD}SPOT{RESET}  {CYAN}{spot:>12,.2f}{RESET}"
        f"   {BOLD}REGIME{RESET}  {reg_color}{regime:<5}{RESET}"
        f"   {BOLD}TRADES{RESET}  {trade_count.get(active_index,0)}/{MAX_TRADES_PER_DAY}"
        f"   {BOLD}COOLDOWN{RESET}  {_cooldown_str(active_index)}"
        f"   {BOLD}LOT SIZE{RESET}  {cfg.get('lot_size','—')}"
        f"   {BOLD}OPEN{RESET}  {pnl['open_positions']}"
    )
    lines.append(sep)

    lines.append(
        f"  {BOLD}REALIZED{RESET}   {pnl_color}₹{pnl['realized']:>+10,.2f}{RESET}"
        f"   {BOLD}UNREALIZED{RESET}  {pnl_color}₹{pnl['unrealized']:>+10,.2f}{RESET}"
        f"   {BOLD}TOTAL{RESET}  {pnl_color}{arrow} ₹{pnl['total']:>+10,.2f}{RESET}"
    )
    lines.append(sep)

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

    if margin_info:
        lines.append(
            f"  {BOLD}MARGIN REQ{RESET}  ₹{margin_info['margin_required']:>10,.2f}"
            f"   {BOLD}NET CREDIT{RESET}  ₹{margin_info['net_credit']:>+6,.2f}/lot"
            f"   {GREEN}{BOLD}MAX PROFIT{RESET}  ₹{margin_info['max_profit']:>8,.2f}{RESET}"
            f"   {RED}{BOLD}MAX LOSS{RESET}  ₹{margin_info['max_loss']:>8,.2f}{RESET}"
        )
        lines.append(sep)

    hdr = f"{'STRATEGY':<16}{'SIDE':<6}{'SYMBOL':<26}{'TYPE':<5}{'STRIKE':>9}{'ENTRY':>8}{'LTP':>8}{'PnL':>10}"
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
                lines.append(
                    f"  {pos['strategy']:<16}"
                    f"{leg['side']:<6}"
                    f"{symbol:<26}"
                    f"{leg['type']:<5}"
                    f"{leg['strike']:>9,.0f}"
                    f"{leg['price']:>8,.2f}"
                    f"{ltp_disp:>8}"
                    f"{lp_color}{leg_pnl:>+10,.2f}{RESET}"
                )

    lines.append(dsep)

    sys.stdout.write("\033[H\033[J")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


# =========================================
# UPDATE LEG LTP
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
async def execute_trade(engine, idx, strategy_name, legs, chain):
    global last_trade_time, trade_count
    try:
        log(f"[{idx}] Executing {strategy_name}")
        trade_legs  = []
        margin_info = None

        for leg in legs:
            side, strike, opt_type, symbol, entry_ltp, margin = leg
            ltp = entry_ltp or get_ltp(chain, strike, opt_type)
            if ltp is None:
                log(f"Missing LTP for {strike} {opt_type}", logging.ERROR)
                return
            margin_info = margin
            trade_legs.append({
                "side":           side,
                "strike":         strike,
                "type":           opt_type,
                "price":          ltp,
                "symbol":         symbol,
                "ltp":            ltp,
                "unrealized_pnl": 0,
            })

        engine.add_position(strategy_name, trade_legs, margin_info)
        last_trade_time[idx] = datetime.datetime.now()
        trade_count[idx]     = trade_count.get(idx, 0) + 1
        log(f"[{idx}] Trade executed: {strategy_name}")

    except Exception:
        log("Trade execution failed", logging.ERROR)
        traceback.print_exc()


# =========================================
# SINGLE INDEX CYCLE
# =========================================
async def run_cycle(broker, model, engine, idx):
    try:
        if not market_open():
            return

        cfg      = INDEX_CONFIG[idx]
        lot_size = cfg["lot_size"]

        candles = fetch_candles(ticker=cfg["yf_ticker"])
        if candles is None or candles.empty:
            return

        regime = predict_regime(model, candles)

        chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
        if not chain:
            return

        engine.mark_to_market(chain)
        _update_leg_ltp(engine, chain)

        pnl         = engine.get_pnl()
        margin_info = engine.get_margin_info()

        set_data(f"pnl_{idx}",    str(pnl))
        set_data(f"spot_{idx}",   spot)
        set_data(f"regime_{idx}", regime)

        atm    = get_atm_strike(chain, spot)
        greeks = greeks_fd(spot, atm + 50, atm - 50)

        render_dashboard(idx, spot, regime, pnl, greeks, engine.positions, margin_info)

        # ── EXIT ──
        if pnl["unrealized"] <= STOP_LOSS:
            log(f"[{idx}] 🛑 Stop loss hit: ₹{pnl['unrealized']:,.2f}", logging.WARNING)
            engine.close_all()
            return

        if pnl["unrealized"] >= TARGET:
            log(f"[{idx}] ✅ Target hit: ₹{pnl['unrealized']:,.2f}")
            engine.close_all()
            return

        # ── ENTRY: blocked if open position exists ──
        if engine.has_open_positions():
            return

        if not can_trade(idx):
            return

        if regime == "SIDE":
            legs = select_strikes(chain, spot, "IRON_CONDOR", target_delta=TARGET_DELTA, lot_size=lot_size)
            if legs:
                await execute_trade(engine, idx, "IRON_CONDOR", legs, chain)

        elif regime == "BULL":
            legs = select_strikes(chain, spot, "BULL_PUT", target_delta=TARGET_DELTA, lot_size=lot_size)
            if legs:
                await execute_trade(engine, idx, "BULL_PUT", legs, chain)

        elif regime == "BEAR":
            legs = select_strikes(chain, spot, "BEAR_CALL", target_delta=TARGET_DELTA, lot_size=lot_size)
            if legs:
                await execute_trade(engine, idx, "BEAR_CALL", legs, chain)

    except Exception:
        log(f"[{idx}] Error in cycle", logging.ERROR)
        traceback.print_exc()


# =========================================
# MAIN
# =========================================
async def main():
    setup_logging()

    broker = Broker()
    model  = load_model()

    # ── Ask user which index ──
    selected_indices = ask_index_selection()

    # ── If AUTO (all), pick best one now ──
    if len(selected_indices) > 1:
        best = pick_best_index(broker, model, selected_indices)
        input(f"\n  Press Enter to start trading {INDEX_CONFIG[best]['label']}...")
        selected_indices = [best]

    active_index = selected_indices[0]
    cfg          = INDEX_CONFIG[active_index]

    print(f"\n  ✅ Trading: {cfg['label']}  |  Lot size: {cfg['lot_size']}")
    print(f"  SL: ₹{STOP_LOSS:,}  |  Target: ₹{TARGET:,}  |  Delta: {TARGET_DELTA}")
    input("  Press Enter to start bot...\n")

    engine = PaperEngine()

    # Init state for this index
    trade_count[active_index]     = 0
    last_trade_time[active_index] = None

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    log(f"Bot started — trading {cfg['label']}")

    while True:
        try:
            await run_cycle(broker, model, engine, active_index)
        except Exception as e:
            log(f"Loop error: {e}", logging.ERROR)
            traceback.print_exc()
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot stopped")