"""
main_async.py  -  Trading Bot Entry Point

Run:  python core/main_async.py

Regime change: closes incompatible positions immediately, enters new ones next cycle.
Market close:  ML-based carry/close decision using trade history win-rate.
"""
import asyncio, datetime as dt, json, logging, os, subprocess, sys, tempfile, traceback
from datetime import date

from data.candles import fetch_candles
from execution.paper_engine import PaperEngine
from broker.upstox import Broker
from infra.redis_bus import set_data
from ml.regime_xgb import load_model, predict_regime
from strategy.strike_selector import select_strikes
from config import SETTINGS, INDEX_CONFIG

POLL_INTERVAL      = SETTINGS.poll_interval_seconds
TRADE_COOLDOWN     = SETTINGS.trade_cooldown_seconds
MAX_TRADES_PER_DAY = SETTINGS.max_trades_per_day
STOP_LOSS          = SETTINGS.stop_loss
TARGET             = SETTINGS.target_profit
TARGET_DELTA       = SETTINGS.target_delta
SPREAD_WIDTH       = SETTINGS.spread_width_points
STATE_FILE         = os.path.join(tempfile.gettempdir(), "trading_bot_state.json")

last_trade_time: dict = {}
trade_count:     dict = {}
_last_regime:    dict = {}
_eod_handled:    dict = {}   # tracks if end-of-day logic ran today

_REGIME_TO_STRATEGY = {"SIDE": "IRON_CONDOR", "BULL": "BULL_PUT", "BEAR": "BEAR_CALL"}
_STRATEGY_INCOMPATIBLE_REGIMES = {
    "IRON_CONDOR": {"BULL", "BEAR"},
    "BULL_PUT":    {"BEAR", "SIDE"},
    "BEAR_CALL":   {"BULL", "SIDE"},
}
# Win-rate threshold: if strategy win-rate >= this, carry overnight; else close
_CARRY_WIN_RATE_THRESHOLD = float(os.getenv("CARRY_WIN_RATE_THRESHOLD", "0.55"))


# -- Logging -----------------------------------------------------------------
_log_buffer: list = []

def setup_logging():
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s",
        level=logging.INFO,
        handlers=[
            logging.FileHandler(SETTINGS.log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

def log(msg, level=logging.INFO):
    logging.log(level, msg)
    # Stream to Redis so /logs endpoint shows live output
    try:
        ist = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)
        ts  = ist.strftime("%H:%M:%S")
        prefix = "ERR" if level >= logging.ERROR else ("WRN" if level >= logging.WARNING else "INF")
        _log_buffer.append(f"[{ts} IST] {prefix} {msg}")
        if len(_log_buffer) > 200:
            _log_buffer.pop(0)
        set_data("bot_logs", json.dumps(_log_buffer[-100:]))
    except Exception:
        pass


# ── IST time ──────────────────────────────────────────────────────────────────
def _ist_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)


# ── Market status ─────────────────────────────────────────────────────────────
def market_status() -> tuple:
    now = _ist_now()
    t   = now.time()
    wd  = now.weekday()
    if wd >= 5:
        return False, f"Weekend ({'Saturday' if wd==5 else 'Sunday'}) - market closed"
    open_t  = SETTINGS.market_open_time
    close_t = SETTINGS.market_close_time
    if t < open_t:
        mins = int((dt.datetime.combine(now.date(), open_t) -
                    dt.datetime.combine(now.date(), t)).total_seconds() // 60)
        return False, f"Pre-market - opens in {mins}m (IST {open_t.strftime('%H:%M')})"
    if t > close_t:
        return False, f"Post-market - closed at IST {close_t.strftime('%H:%M')}"
    return True, f"Market OPEN (IST {t.strftime('%H:%M:%S')})"

def market_open() -> bool:
    return market_status()[0]

def _is_near_close() -> bool:
    """True if within 15 min of market close."""
    now = _ist_now().time()
    close_t = SETTINGS.market_close_time
    close_dt = dt.datetime.combine(dt.date.today(), close_t)
    now_dt   = dt.datetime.combine(dt.date.today(), now)
    return dt.timedelta(0) <= (close_dt - now_dt) <= dt.timedelta(minutes=15)

def can_trade(idx: str) -> bool:
    if trade_count.get(idx, 0) >= MAX_TRADES_PER_DAY:
        return False
    lt = last_trade_time.get(idx)
    if lt and (dt.datetime.now() - lt).seconds < TRADE_COOLDOWN:
        return False
    return True

def _regime_conflicts(pos: dict, regime: str) -> bool:
    return regime in _STRATEGY_INCOMPATIBLE_REGIMES.get(pos.get("strategy", ""), set())


# ── Market-close carry/close decision ─────────────────────────────────────────
def _should_carry_position(pos: dict) -> bool:
    """
    Decide whether to carry a position overnight based on:
    1. Historical win-rate of this strategy from trade history
    2. Current unrealized P&L (don't carry big losers)
    Returns True = carry overnight, False = close now.
    """
    from execution.paper_engine import load_trade_history
    strategy = pos.get("strategy", "")
    history  = load_trade_history()

    # Filter history for this strategy
    strat_trades = [h for h in history if h.get("strategy") == strategy]
    if len(strat_trades) >= 5:
        wins     = sum(1 for h in strat_trades if float(h.get("pnl", 0)) > 0)
        win_rate = wins / len(strat_trades)
        log(f"  Carry check: {strategy} win_rate={win_rate:.1%} "
            f"(threshold={_CARRY_WIN_RATE_THRESHOLD:.1%})")
        if win_rate < _CARRY_WIN_RATE_THRESHOLD:
            log(f"  -> CLOSE (win_rate {win_rate:.1%} < threshold)")
            return False
    else:
        log(f"  Carry check: {strategy} insufficient history ({len(strat_trades)} trades) -> CLOSE")
        return False

    # Don't carry if already at a significant loss (>50% of max loss)
    unrealized = float(pos.get("unrealized", 0))
    max_loss   = float(pos.get("max_loss", 1))
    if max_loss > 0 and unrealized < -(max_loss * 0.5):
        log(f"  -> CLOSE (unrealized Rs{unrealized:,.0f} > 50% of max_loss Rs{max_loss:,.0f})")
        return False

    log(f"  -> CARRY overnight (win_rate OK, loss within limits)")
    return True


# ── Step 1: Token check (loads from Redis or env var) ─────────────────────────
def auto_authenticate() -> bool:
    """
    Check if a valid Upstox token is available.
    On Render: token is stored in Redis after user visits /auth
    Locally:   token is in core/.env as UPSTOX_ACCESS_TOKEN
    """
    import requests
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(_env, override=True)

    # 1. Try Redis first (set by /auth/callback on Render)
    token = None
    try:
        from infra.redis_bus import get_data
        t = get_data("upstox_access_token")
        if t and len(t) > 20:
            token = t
            os.environ["UPSTOX_ACCESS_TOKEN"] = t
            log("Token loaded from Redis")
    except Exception:
        pass

    # 2. Fallback to env var
    if not token:
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

    # 3. Validate token
    if token and len(token) > 20:
        try:
            resp = requests.get(
                "https://api.upstox.com/v2/user/profile",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=8,
            )
            if resp.status_code == 200:
                name = resp.json().get("data", {}).get("user_name", "")
                log(f"Auth OK - logged in as: {name or 'Upstox User'}")
                return True
            log(f"Token invalid (HTTP {resp.status_code})", logging.WARNING)
        except Exception as exc:
            log(f"Token check failed: {exc}", logging.WARNING)

    # 4. Token missing or expired
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        log("=" * 60, logging.WARNING)
        log("TOKEN EXPIRED — Open this URL in your browser to refresh:", logging.WARNING)
        log(f"  {render_url}/auth", logging.WARNING)
        log("Takes 10 seconds. Bot will resume automatically.", logging.WARNING)
        log("=" * 60, logging.WARNING)
    else:
        log("TOKEN EXPIRED — Run:  python core/broker/auth.py", logging.WARNING)
    return False


async def wait_for_valid_token(max_wait_minutes: int = 30) -> bool:
    """
    Wait for a valid token to appear in Redis (user visits /auth).
    Called when token is expired at startup or during trading day.
    Checks every 30 seconds for up to max_wait_minutes.
    """
    import requests
    log(f"Waiting up to {max_wait_minutes} min for token refresh via /auth...")
    for _ in range(max_wait_minutes * 2):   # check every 30s
        await asyncio.sleep(30)
        try:
            from infra.redis_bus import get_data
            t = get_data("upstox_access_token")
            if t and len(t) > 20:
                resp = requests.get(
                    "https://api.upstox.com/v2/user/profile",
                    headers={"Authorization": f"Bearer {t}", "Accept": "application/json"},
                    timeout=8,
                )
                if resp.status_code == 200:
                    os.environ["UPSTOX_ACCESS_TOKEN"] = t
                    name = resp.json().get("data", {}).get("user_name", "")
                    log(f"Token refreshed! Logged in as: {name or 'Upstox User'}")
                    return True
        except Exception:
            pass
        log("Still waiting for token refresh...", logging.WARNING)
    log("Token refresh timeout. Bot stopping.", logging.ERROR)
    return False


# ── Step 2: Launch dashboard ──────────────────────────────────────────────────
def launch_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    if not os.path.exists(dashboard_path):
        log("Dashboard not found - skipping", logging.WARNING)
        return None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", dashboard_path,
             "--server.headless", "true", "--server.port", "8501",
             "--browser.gatherUsageStats", "false"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log("Dashboard started at http://localhost:8501")
        return proc
    except Exception as exc:
        log(f"Dashboard launch failed: {exc}", logging.WARNING)
        return None


# ── State writer ──────────────────────────────────────────────────────────────
def _write_state(engine: PaperEngine, idx: str, spot, regime: str):
    """Write full bot state to STATE_FILE + Redis for dashboard."""
    try:
        pnl = engine.get_pnl()
        positions_out = [
            {
                "strategy":    p["strategy"],
                "index":       p.get("index", idx),
                "entry_time":  p.get("entry_time", ""),
                "open":        p["open"],
                "unrealized":  p.get("unrealized", 0),
                "max_profit":  p.get("max_profit", 0),
                "max_loss":    p.get("max_loss",   0),
                "net_credit":  p.get("net_credit", 0),
                "margin_info": p.get("margin_info", {}),
                "legs":        p.get("legs", []),
            }
            for p in engine.positions
        ]
        data = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[f"spot_{idx}"]   = str(spot)
        data[f"regime_{idx}"] = regime
        data[f"pnl_{idx}"]    = json.dumps(pnl, default=str)
        data["all_positions"] = json.dumps({idx: positions_out}, default=str)
        data["last_update"]   = dt.datetime.now().isoformat(timespec="seconds")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        set_data(f"spot_{idx}",   str(spot))
        set_data(f"regime_{idx}", regime)
        set_data(f"pnl_{idx}",    json.dumps(pnl, default=str))
        # Merge all_positions across indices (don't overwrite other indices)
        try:
            from infra.redis_bus import get_data as _gd
            existing_raw = _gd("all_positions")
            all_pos = json.loads(existing_raw) if existing_raw else {}
        except Exception:
            all_pos = {}
        all_pos[idx] = positions_out
        set_data("all_positions", json.dumps(all_pos, default=str))
        set_data("last_update",   data["last_update"])
    except Exception as exc:
        log(f"_write_state: {exc}", logging.WARNING)


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_ltp(chain, strike, opt_type):
    for row in chain:
        if row["strikePrice"] == strike:
            return row[opt_type].get("ltp")
    return None


# ── Index scanner ─────────────────────────────────────────────────────────────
def pick_best_index(broker, model, indices):
    log("Scanning indices for best opportunity...")
    scores = {}
    for idx in indices:
        try:
            cfg     = INDEX_CONFIG[idx]
            candles = fetch_candles(ticker=cfg["yf_ticker"], broker=broker)
            if candles is None or candles.empty:
                continue
            chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"], spot=spot)
            if not chain or not spot:
                continue
            atm    = min(chain, key=lambda x: abs(x["strikePrice"] - spot))["strikePrice"]
            nearby = [r for r in chain if abs(r["strikePrice"] - atm) <= 300]
            avg_oi = sum(
                (r.get("CE", {}).get("oi", 50000) + r.get("PE", {}).get("oi", 50000))
                for r in nearby
            ) / max(len(nearby), 1)
            rs = {"SIDE": 3, "BULL": 2, "BEAR": 2}.get(regime, 1)
            scores[idx] = {"score": rs * (avg_oi / 100000), "regime": regime,
                           "spot": spot, "label": cfg["label"]}
            log(f"  {cfg['label']:<14} Spot:{spot:>10,.2f}  Regime:{regime:<5}  Score:{scores[idx]['score']:.2f}")
        except Exception as exc:
            log(f"  {idx}: {exc}", logging.ERROR)
    if not scores:
        log("No index scored - defaulting to NIFTY", logging.WARNING)
        return "NIFTY"
    best = max(scores, key=lambda x: scores[x]["score"])
    log(f"Best index: {scores[best]['label']}  (score {scores[best]['score']:.2f})")
    return best


# ── Trade execution ───────────────────────────────────────────────────────────
async def execute_trade(engine, idx, strategy_name, legs, chain):
    global last_trade_time, trade_count
    try:
        trade_legs, margin_info = [], None
        for leg in legs:
            side, strike, opt_type, symbol, entry_ltp, margin = leg
            ltp = entry_ltp if entry_ltp is not None else get_ltp(chain, strike, opt_type)
            if ltp is None:
                log(f"Missing LTP {strike} {opt_type}", logging.ERROR)
                return
            margin_info = margin
            trade_legs.append({
                "side": side, "strike": strike, "type": opt_type,
                "price": ltp, "symbol": symbol, "ltp": ltp,
                "qty": INDEX_CONFIG[idx]["lot_size"], "unrealized_pnl": 0,
            })
        engine.add_position(strategy_name, trade_legs, margin_info, index=idx)
        last_trade_time[idx] = dt.datetime.now()
        trade_count[idx]     = trade_count.get(idx, 0) + 1
        log(f"[{idx}] NEW POSITION: {strategy_name}  ({'DRY RUN' if SETTINGS.dry_run else 'LIVE'})")
        # Log legs
        for tl in trade_legs:
            log(f"[{idx}]   {tl['side']} {tl['type']} {tl['strike']:,.0f}  "
                f"Entry:Rs{tl['price']:.2f}  Qty:{tl['qty']}")
        log(f"[{idx}]   MaxProfit:Rs{margin_info.get('max_profit',0):,.0f}  "
            f"MaxLoss:Rs{margin_info.get('max_loss',0):,.0f}  "
            f"NetCredit:Rs{margin_info.get('net_credit',0):,.0f}")
    except Exception:
        log("Trade execution failed", logging.ERROR)
        traceback.print_exc()


# ── End-of-day carry/close logic ──────────────────────────────────────────────
async def handle_end_of_day(engine: PaperEngine, idx: str, spot, regime: str):
    """
    Called when market is within 15 min of close.
    For each open position, decide carry vs close based on ML history.
    """
    today = date.today().isoformat()
    if _eod_handled.get(idx) == today:
        return   # already handled today
    _eod_handled[idx] = today

    open_positions = [p for p in engine.positions if p["open"]]
    if not open_positions:
        return

    log(f"[{idx}] === END OF DAY: {len(open_positions)} open position(s) ===")
    for pos in open_positions:
        log(f"[{idx}] Evaluating: {pos['strategy']}  "
            f"Unrealized:Rs{pos.get('unrealized',0):+,.0f}")
        carry = _should_carry_position(pos)
        if carry:
            engine.close_position(pos, exit_reason="EOD_CARRY_OVERNIGHT")
            log(f"[{idx}] Position marked as CARRIED OVERNIGHT (will re-enter tomorrow)")
        else:
            engine.close_position(pos, exit_reason="EOD_CLOSE")
            log(f"[{idx}] Position CLOSED at end of day  "
                f"PnL:Rs{pos.get('unrealized',0):+,.0f}")
    _write_state(engine, idx, spot, regime)


# ── Main trading cycle ────────────────────────────────────────────────────────
_last_closed_log = None

async def run_cycle(broker, model, engine, idx, spot: float | None = None):
    global _last_closed_log, _last_regime
    try:
        is_open, status_msg = market_status()

        # ── End-of-day check (runs even if market just closed) ────────────────
        if _is_near_close() or not is_open:
            chain_eod, spot_eod = None, None
            try:
                chain_eod, spot_eod = broker.get_option_chain(idx, range_size=INDEX_CONFIG[idx]["range_size"], spot=spot)
            except Exception:
                pass
            if chain_eod and spot_eod:
                engine.mark_to_market(chain_eod)
            regime_eod = _last_regime.get(idx, "SIDE")
            await handle_end_of_day(engine, idx, spot_eod or 0, regime_eod)

        if not is_open:
            now = dt.datetime.now()
            if _last_closed_log is None or (now - _last_closed_log).seconds >= 300:
                log(f"[{idx}] {status_msg}")
                _last_closed_log = now
            # Write heartbeat so dashboard shows bot is alive even when market closed
            set_data(f"regime_{idx}", _last_regime.get(idx, "SIDE"))
            set_data("last_update", dt.datetime.now().isoformat(timespec="seconds"))
            # Write zero PnL so dashboard shows Rs0 instead of dashes
            try:
                from infra.redis_bus import get_data as _gd
                if not _gd(f"pnl_{idx}"):
                    zero_pnl = {"realized":0,"unrealized":0,"total":0,
                                "open_positions":0,"today_realized":0,"today_trades":0,
                                "max_profit":0,"max_loss":0,"net_credit":0}
                    set_data(f"pnl_{idx}", json.dumps(zero_pnl))
                if not _gd(f"spot_{idx}"):
                    set_data(f"spot_{idx}", "0")
            except Exception:
                pass
            return

        cfg      = INDEX_CONFIG[idx]
        lot_size = cfg["lot_size"]

        candles = fetch_candles(ticker=cfg["yf_ticker"], broker=broker)
        if candles is None or candles.empty:
            log(f"[{idx}] No candle data", logging.WARNING)
            return

        regime = predict_regime(model, candles)

        chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"], spot=spot)
        if not chain:
            log(f"[{idx}] No option chain data", logging.WARNING)
            return

        engine.mark_to_market(chain)
        pnl = engine.get_pnl()

        # ── Regime change detection ───────────────────────────────────────────
        prev_regime    = _last_regime.get(idx)
        regime_changed = prev_regime is not None and prev_regime != regime
        if regime_changed:
            log(f"[{idx}] *** REGIME CHANGE: {prev_regime} -> {regime} ***", logging.WARNING)

        conflicting = [p for p in engine.positions if p["open"] and _regime_conflicts(p, regime)]
        if conflicting:
            strats = ", ".join(p["strategy"] for p in conflicting)
            log(f"[{idx}] Closing {len(conflicting)} incompatible position(s) [{strats}]", logging.WARNING)
            for pos in conflicting:
                engine.close_position(pos, exit_reason=f"REGIME_CHANGE_{prev_regime}_TO_{regime}")
            pnl = engine.get_pnl()
            _write_state(engine, idx, spot, regime)

        _last_regime[idx] = regime
        _write_state(engine, idx, spot, regime)

        # ── Terminal log ──────────────────────────────────────────────────────
        ist      = _ist_now()
        open_pos = pnl["open_positions"]
        regime_display = f"{prev_regime}->{regime}" if regime_changed else regime
        log(f"[{idx}] {ist.strftime('%H:%M:%S')} IST | Spot:{spot:,.0f} | "
            f"Regime:{regime_display} | OpenPos:{open_pos} | "
            f"Unrealized:Rs{pnl['unrealized']:+,.0f} | "
            f"TodayPnL:Rs{pnl['today_realized']:+,.0f} | "
            f"Total:Rs{pnl['total']:+,.0f}")
        if open_pos > 0:
            log(f"[{idx}]   MaxProfit:Rs{pnl['max_profit']:,.0f} | "
                f"MaxLoss:Rs{pnl['max_loss']:,.0f} | "
                f"NetCredit:Rs{pnl['net_credit']:,.0f} | "
                f"TodayTrades:{pnl['today_trades']}")
            for pos in engine.positions:
                if not pos["open"]:
                    continue
                for leg in pos.get("legs", []):
                    log(f"[{idx}]     {leg.get('side','')} {leg.get('type','')} "
                        f"{leg.get('strike',0):,.0f} | "
                        f"Entry:Rs{leg.get('price',0):.2f} | "
                        f"LTP:Rs{leg.get('ltp',0):.2f} | "
                        f"LegPnL:Rs{leg.get('unrealized_pnl',0):+,.0f}")

        # ── Stop loss / target ────────────────────────────────────────────────
        if pnl["unrealized"] <= STOP_LOSS:
            log(f"[{idx}] STOP LOSS hit: Rs{pnl['unrealized']:,.0f}", logging.WARNING)
            engine.close_all(exit_reason="STOP_LOSS")
            _write_state(engine, idx, spot, regime)
            return
        if pnl["unrealized"] >= TARGET:
            log(f"[{idx}] TARGET hit: Rs{pnl['unrealized']:,.0f}")
            engine.close_all(exit_reason="TARGET")
            _write_state(engine, idx, spot, regime)
            return

        # ── Check close commands from dashboard ───────────────────────────────
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                cmds = sdata.get("close_commands", [])
                if isinstance(cmds, str):
                    cmds = json.loads(cmds)
                remaining = []
                for cmd in cmds:
                    if cmd.get("index") == idx and cmd.get("action") == "close_all":
                        log(f"[{idx}] Manual close command received from dashboard")
                        engine.close_all(exit_reason="MANUAL_DASHBOARD")
                        _write_state(engine, idx, spot, regime)
                    elif cmd.get("action") == "close_position":
                        pos_idx = cmd.get("position_index", -1)
                        open_positions = [p for p in engine.positions if p["open"]]
                        if 0 <= pos_idx < len(open_positions):
                            engine.close_position(open_positions[pos_idx],
                                                  exit_reason="MANUAL_DASHBOARD")
                            _write_state(engine, idx, spot, regime)
                    else:
                        remaining.append(cmd)
                sdata["close_commands"] = remaining
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(sdata, f, default=str)
        except Exception as exc:
            log(f"[{idx}] Close command check failed: {exc}", logging.WARNING)

        # ── Entry ─────────────────────────────────────────────────────────────
        if engine.has_open_positions() or not can_trade(idx):
            return

        expiry_str = broker.get_nearest_expiry(idx)
        T = 0.1
        if expiry_str:
            try:
                expiry_date = dt.datetime.strptime(expiry_str, "%Y-%m-%d").date()
                T = max((expiry_date - date.today()).days / 365, 1 / 365)
            except Exception:
                pass

        strategy = _REGIME_TO_STRATEGY.get(regime)
        if not strategy:
            return

        legs = select_strikes(chain, spot, strategy, T=T,
                              target_delta=TARGET_DELTA, lot_size=lot_size,
                              spread_width=SPREAD_WIDTH)
        if legs:
            await execute_trade(engine, idx, strategy, legs, chain)
            _write_state(engine, idx, spot, regime)
        else:
            log(f"[{idx}] {strategy}: no legs returned", logging.WARNING)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        log(f"[{idx}] Cycle error: {exc}", logging.ERROR)
        log(tb, logging.ERROR)
        # Write error to Redis so dashboard can show it
        set_data(f"cycle_error_{idx}", f"{exc} | {dt.datetime.now().isoformat()}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    setup_logging()
    log("=" * 60)
    log("  ALGO TRADING BOT  -  Starting up")
    log("=" * 60)
    log("Step 1/4: Checking Upstox authentication...")
    if not auto_authenticate():
        log("Token expired — waiting for refresh via /auth endpoint...", logging.WARNING)
        refreshed = await wait_for_valid_token(max_wait_minutes=30)
        if not refreshed:
            log("Authentication failed after 30 min wait.", logging.ERROR)
            log("Open /auth in browser or run: python core/broker/auth.py", logging.ERROR)
            sys.exit(1)
    log("Step 2/4: Launching dashboard...")
    dash_proc = launch_dashboard()
    if dash_proc:
        log("Dashboard: http://localhost:8501")
    log("Step 3/4: Loading regime model...")
    broker = Broker()
    model  = load_model(broker=broker)
    log("Model ready")
    log("Step 4/4: Starting trading loop for all active indices...")
    active_indices = list(SETTINGS.active_indices) or list(INDEX_CONFIG.keys())
    is_open, status_msg = market_status()
    ist = _ist_now()
    log("-" * 60)
    log(f"  Indices    : {', '.join(active_indices)}")
    log(f"  Stop Loss  : Rs{STOP_LOSS:,}")
    log(f"  Target     : Rs{TARGET:,}")
    log(f"  Delta      : {TARGET_DELTA}")
    log(f"  Spread     : {SPREAD_WIDTH} pts")
    log(f"  Mode       : {'DRY RUN (paper trading)' if SETTINGS.dry_run else 'LIVE TRADING'}")
    log(f"  IST Time   : {ist.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  Market     : {status_msg}")
    log(f"  Hours      : {SETTINGS.market_open_time.strftime('%H:%M')} - "
        f"{SETTINGS.market_close_time.strftime('%H:%M')} IST  (Mon-Fri)")
    log(f"  Carry threshold: {_CARRY_WIN_RATE_THRESHOLD:.0%} win-rate")
    log("-" * 60)

    # One PaperEngine per index
    engines = {idx: PaperEngine() for idx in active_indices}
    for idx in active_indices:
        trade_count[idx]     = 0
        last_trade_time[idx] = None

    log("Bot running. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                # Pre-fetch ALL spot prices in ONE batch API call
                # This avoids 3 separate calls hitting rate limit
                log(f"Fetching spots for {active_indices}...")
                spot_map = broker.get_spot_batch(active_indices)
                log(f"Spots: { {k: round(v,2) for k,v in spot_map.items()} }")

                # Run indices SEQUENTIALLY - not concurrently
                # Concurrent calls = multiple 429s simultaneously
                for idx in active_indices:
                    await run_cycle(broker, model, engines[idx], idx,
                                    spot=spot_map.get(idx))
            except Exception as exc:
                log(f"Loop error: {exc}", logging.ERROR)
                traceback.print_exc()
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        if dash_proc and dash_proc.poll() is None:
            dash_proc.terminate()
            log("Dashboard stopped.")
        log("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot stopped by user.")


