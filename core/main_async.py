"""
main_async.py  -  Trading Bot Entry Point

Run:  python core/main_async.py
  1. Auto-authenticate  - checks token, refreshes if expired
  2. Launch dashboard   - Streamlit at http://localhost:8501
  3. Load/train model   - XGBoost regime model
  4. Scan indices       - picks best by regime + OI score
  5. Trading loop       - runs every POLL_INTERVAL_SECONDS (IST-aware)

Regime change logic:
  Every cycle the model predicts the current regime (BULL/BEAR/SIDE).
  If the regime changes AND the open position strategy is incompatible,
  the position is closed immediately and a new one is opened next cycle.

  Compatibility table:
    IRON_CONDOR  needs SIDE  -> close if BULL or BEAR
    BULL_PUT     needs BULL  -> close if BEAR or SIDE
    BEAR_CALL    needs BEAR  -> close if BULL or SIDE
"""
import asyncio, datetime, json, logging, os, subprocess, sys, tempfile, traceback
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
_last_regime:    dict = {}   # tracks last known regime per index

# Strategy <-> Regime mapping
_REGIME_TO_STRATEGY = {
    "SIDE": "IRON_CONDOR",
    "BULL": "BULL_PUT",
    "BEAR": "BEAR_CALL",
}

# Regimes that are INCOMPATIBLE with each strategy
# If current regime is in this set -> close the position immediately
_STRATEGY_INCOMPATIBLE_REGIMES = {
    "IRON_CONDOR": {"BULL", "BEAR"},   # condor needs sideways; close if trending
    "BULL_PUT":    {"BEAR", "SIDE"},   # bull put needs bullish; close if bearish/flat
    "BEAR_CALL":   {"BULL", "SIDE"},   # bear call needs bearish; close if bullish/flat
}


# ── Logging ───────────────────────────────────────────────────────────────────
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


# ── IST time helper ───────────────────────────────────────────────────────────
def _ist_now() -> datetime.datetime:
    utc_now    = datetime.datetime.utcnow()
    ist_offset = datetime.timedelta(hours=5, minutes=30)
    return utc_now + ist_offset


# ── Market status ─────────────────────────────────────────────────────────────
def market_status() -> tuple:
    now = _ist_now()
    t   = now.time()
    wd  = now.weekday()

    if wd >= 5:
        day = "Saturday" if wd == 5 else "Sunday"
        return False, f"Weekend ({day}) - market closed"

    open_t  = SETTINGS.market_open_time
    close_t = SETTINGS.market_close_time

    if t < open_t:
        opens_in = datetime.datetime.combine(now.date(), open_t) - \
                   datetime.datetime.combine(now.date(), t)
        mins = int(opens_in.total_seconds() // 60)
        return False, f"Pre-market - opens in {mins}m (IST {open_t.strftime('%H:%M')})"

    if t > close_t:
        return False, f"Post-market - closed at IST {close_t.strftime('%H:%M')}"

    return True, f"Market OPEN (IST {t.strftime('%H:%M:%S')})"


def market_open() -> bool:
    is_open, _ = market_status()
    return is_open


def can_trade(idx: str) -> bool:
    if trade_count.get(idx, 0) >= MAX_TRADES_PER_DAY:
        return False
    lt = last_trade_time.get(idx)
    if lt and (datetime.datetime.now() - lt).seconds < TRADE_COOLDOWN:
        return False
    return True


def _regime_conflicts(pos: dict, current_regime: str) -> bool:
    """
    Returns True if the open position's strategy is incompatible
    with the current regime and must be closed.
    """
    strategy    = pos.get("strategy", "")
    bad_regimes = _STRATEGY_INCOMPATIBLE_REGIMES.get(strategy, set())
    return current_regime in bad_regimes


# ── Step 1: Auto-authenticate ─────────────────────────────────────────────────
def auto_authenticate() -> bool:
    import requests, re
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(_env, override=True)

    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

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
            log(f"Token invalid (HTTP {resp.status_code}) - need to refresh", logging.WARNING)
        except Exception as exc:
            log(f"Token check failed: {exc}", logging.WARNING)

    api_key      = os.getenv("UPSTOX_API_KEY", "").strip()
    api_secret   = os.getenv("UPSTOX_API_SECRET", "").strip()
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1").strip()
    auth_code    = os.getenv("UPSTOX_AUTH_CODE", "").strip()
    bad          = ("", "your_api_key_here", "your_secret_key_here",
                    "paste_auth_code_here", "your_token_here")

    if api_key in bad or api_secret in bad:
        log("UPSTOX_API_KEY / UPSTOX_API_SECRET not set in .env", logging.ERROR)
        return False

    if not auth_code or auth_code in bad:
        from urllib.parse import urlencode
        params = {"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri}
        url = f"https://api.upstox.com/v2/login/authorization/dialog?{urlencode(params)}"
        log("Access token expired. Run:  python core/broker/auth.py", logging.WARNING)
        log(f"Or open: {url}", logging.WARNING)
        return False

    log("Exchanging auth code for access token...")
    try:
        resp = requests.post(
            "https://api.upstox.com/v2/login/authorization/token",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
            data={"code": auth_code, "client_id": api_key, "client_secret": api_secret,
                  "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
            timeout=15,
        )
        if resp.status_code != 200:
            log(f"Token exchange failed: {resp.status_code}", logging.ERROR)
            return False
        new_token = resp.json().get("access_token", "")
        if not new_token:
            log("No access_token in response", logging.ERROR)
            return False
        content = open(_env, "r", encoding="utf-8").read()
        new_line = f"UPSTOX_ACCESS_TOKEN={new_token}"
        if re.search(r"^UPSTOX_ACCESS_TOKEN=.*$", content, re.MULTILINE):
            content = re.sub(r"^UPSTOX_ACCESS_TOKEN=.*$", new_line, content, re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{new_line}\n"
        content = re.sub(r"^UPSTOX_AUTH_CODE=.*$", "UPSTOX_AUTH_CODE=",
                         content, flags=re.MULTILINE)
        with open(_env, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.environ["UPSTOX_ACCESS_TOKEN"] = new_token
        log("New access token saved to .env")
        return True
    except Exception as exc:
        log(f"Token exchange error: {exc}", logging.ERROR)
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
             "--server.headless", "true",
             "--server.port", "8501",
             "--browser.gatherUsageStats", "false"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log("Dashboard started at http://localhost:8501")
        return proc
    except FileNotFoundError:
        log("streamlit not installed - run: pip install streamlit", logging.WARNING)
        return None
    except Exception as exc:
        log(f"Dashboard launch failed: {exc}", logging.WARNING)
        return None


# ── State writer ──────────────────────────────────────────────────────────────
def _write_state(engine, idx: str, spot, regime: str):
    """Write full bot state to STATE_FILE for dashboard consumption."""
    try:
        pnl = engine.get_pnl()
        positions_out = []
        for pos in engine.positions:
            positions_out.append({
                "strategy":    pos["strategy"],
                "index":       pos.get("index", idx),
                "entry_time":  pos.get("entry_time", ""),
                "open":        pos["open"],
                "unrealized":  pos.get("unrealized", 0),
                "max_profit":  pos.get("max_profit", 0),
                "max_loss":    pos.get("max_loss",   0),
                "net_credit":  pos.get("net_credit", 0),
                "margin_info": pos.get("margin_info", {}),
                "legs":        pos.get("legs", []),
            })

        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

        data[f"spot_{idx}"]   = str(spot)
        data[f"regime_{idx}"] = regime
        data[f"pnl_{idx}"]    = json.dumps(pnl, default=str)
        data["all_positions"] = json.dumps({idx: positions_out}, default=str)
        data["last_update"]   = datetime.datetime.now().isoformat(timespec="seconds")

        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)

        set_data(f"spot_{idx}",   str(spot))
        set_data(f"regime_{idx}", regime)
        set_data(f"pnl_{idx}",    json.dumps(pnl, default=str))
        set_data("all_positions", json.dumps({idx: positions_out}, default=str))

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
            regime      = predict_regime(model, candles)
            chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
            if not chain or not spot:
                continue
            atm    = min(chain, key=lambda x: abs(x["strikePrice"] - spot))["strikePrice"]
            nearby = [r for r in chain if abs(r["strikePrice"] - atm) <= 300]
            avg_oi = sum(
                (r.get("CE", {}).get("oi", 50000) + r.get("PE", {}).get("oi", 50000))
                for r in nearby
            ) / max(len(nearby), 1)
            rs = {"SIDE": 3, "BULL": 2, "BEAR": 2}.get(regime, 1)
            scores[idx] = {
                "score":  rs * (avg_oi / 100000),
                "regime": regime,
                "spot":   spot,
                "label":  cfg["label"],
            }
            log(f"  {cfg['label']:<14} Spot:{spot:>10,.2f}  "
                f"Regime:{regime:<5}  Score:{scores[idx]['score']:.2f}")
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
        trade_legs  = []
        margin_info = None
        for leg in legs:
            side, strike, opt_type, symbol, entry_ltp, margin = leg
            ltp = entry_ltp if entry_ltp is not None else get_ltp(chain, strike, opt_type)
            if ltp is None:
                log(f"Missing LTP {strike} {opt_type}", logging.ERROR)
                return
            margin_info = margin
            trade_legs.append({
                "side":          side,
                "strike":        strike,
                "type":          opt_type,
                "price":         ltp,
                "symbol":        symbol,
                "ltp":           ltp,
                "qty":           INDEX_CONFIG[idx]["lot_size"],
                "unrealized_pnl": 0,
            })
        engine.add_position(strategy_name, trade_legs, margin_info, index=idx)
        last_trade_time[idx] = datetime.datetime.now()
        trade_count[idx]     = trade_count.get(idx, 0) + 1
        log(f"[{idx}] NEW POSITION: {strategy_name}  "
            f"({'DRY RUN' if SETTINGS.dry_run else 'LIVE'})")
    except Exception:
        log("Trade execution failed", logging.ERROR)
        traceback.print_exc()


# ── Main trading cycle ────────────────────────────────────────────────────────
_last_closed_log = None   # throttle "market closed" log to once per 5 min

async def run_cycle(broker, model, engine, idx):
    global _last_closed_log, _last_regime
    try:
        is_open, status_msg = market_status()
        if not is_open:
            now = datetime.datetime.now()
            if _last_closed_log is None or (now - _last_closed_log).seconds >= 300:
                log(f"[{idx}] {status_msg}")
                _last_closed_log = now
            return

        cfg      = INDEX_CONFIG[idx]
        lot_size = cfg["lot_size"]

        candles = fetch_candles(ticker=cfg["yf_ticker"], broker=broker)
        if candles is None or candles.empty:
            log(f"[{idx}] No candle data", logging.WARNING)
            return

        regime      = predict_regime(model, candles)
        chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
        if not chain:
            log(f"[{idx}] No option chain data", logging.WARNING)
            return

        # Mark to market (updates leg LTP + unrealized_pnl)
        engine.mark_to_market(chain)
        pnl = engine.get_pnl()

        # ── Regime change detection & incompatible position close ─────────────
        prev_regime = _last_regime.get(idx)
        regime_changed = prev_regime is not None and prev_regime != regime

        if regime_changed:
            log(
                f"[{idx}] *** REGIME CHANGE: {prev_regime} -> {regime} ***",
                logging.WARNING,
            )

        # Find open positions that conflict with the current regime
        conflicting = [
            pos for pos in engine.positions
            if pos["open"] and _regime_conflicts(pos, regime)
        ]

        if conflicting:
            strats = ", ".join(p["strategy"] for p in conflicting)
            log(
                f"[{idx}] Closing {len(conflicting)} incompatible position(s) "
                f"[{strats}] — strategy does not match regime={regime}",
                logging.WARNING,
            )
            for pos in conflicting:
                engine.close_position(pos, exit_reason=f"REGIME_CHANGE_{prev_regime}_TO_{regime}")
            pnl = engine.get_pnl()   # refresh after close
            _write_state(engine, idx, spot, regime)
            log(
                f"[{idx}] Incompatible positions closed. "
                f"Realized P&L: Rs{pnl['realized']:+,.0f}  "
                f"New position will be entered next cycle."
            )

        # Update tracked regime
        _last_regime[idx] = regime

        # Write state for dashboard
        _write_state(engine, idx, spot, regime)

        # ── Terminal log ──────────────────────────────────────────────────────
        ist      = _ist_now()
        open_pos = pnl["open_positions"]
        regime_display = (
            f"{prev_regime}->{regime}" if regime_changed else regime
        )
        log(
            f"[{idx}] {ist.strftime('%H:%M:%S')} IST | "
            f"Spot:{spot:,.0f} | Regime:{regime_display} | "
            f"OpenPos:{open_pos} | "
            f"Unrealized:Rs{pnl['unrealized']:+,.0f} | "
            f"TodayPnL:Rs{pnl['today_realized']:+,.0f} | "
            f"Total:Rs{pnl['total']:+,.0f}"
        )

        if open_pos > 0:
            log(
                f"[{idx}]   MaxProfit:Rs{pnl['max_profit']:,.0f} | "
                f"MaxLoss:Rs{pnl['max_loss']:,.0f} | "
                f"NetCredit:Rs{pnl['net_credit']:,.0f} | "
                f"TodayTrades:{pnl['today_trades']}"
            )
            for pos in engine.positions:
                if not pos["open"]:
                    continue
                for leg in pos.get("legs", []):
                    log(
                        f"[{idx}]     {leg.get('side','')} {leg.get('type','')} "
                        f"{leg.get('strike',0):,.0f} | "
                        f"Entry:Rs{leg.get('price',0):.2f} | "
                        f"LTP:Rs{leg.get('ltp',0):.2f} | "
                        f"LegPnL:Rs{leg.get('unrealized_pnl',0):+,.0f}"
                    )

        # ── Stop loss / target exit ───────────────────────────────────────────
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

        # ── Entry: only if no open positions and within trade limits ──────────
        if engine.has_open_positions() or not can_trade(idx):
            return

        expiry_str = broker.get_nearest_expiry(idx)
        T = 0.1
        if expiry_str:
            try:
                expiry_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d").date()
                T = max((expiry_date - date.today()).days / 365, 1 / 365)
            except Exception:
                pass

        strategy = _REGIME_TO_STRATEGY.get(regime)
        if not strategy:
            return

        legs = select_strikes(
            chain, spot, strategy, T=T,
            target_delta=TARGET_DELTA,
            lot_size=lot_size,
            spread_width=SPREAD_WIDTH,
        )
        if legs:
            await execute_trade(engine, idx, strategy, legs, chain)
            _write_state(engine, idx, spot, regime)
        else:
            log(f"[{idx}] {strategy}: no legs returned", logging.WARNING)

    except Exception:
        log(f"[{idx}] Cycle error", logging.ERROR)
        traceback.print_exc()


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    setup_logging()

    log("=" * 60)
    log("  ALGO TRADING BOT  -  Starting up")
    log("=" * 60)

    log("Step 1/4: Checking Upstox authentication...")
    if not auto_authenticate():
        log("Authentication failed. Run:  python core/broker/auth.py", logging.ERROR)
        sys.exit(1)

    log("Step 2/4: Launching dashboard...")
    dash_proc = launch_dashboard()
    if dash_proc:
        log("Dashboard: http://localhost:8501")

    log("Step 3/4: Loading regime model...")
    broker = Broker()
    model  = load_model(broker=broker)
    log("Model ready")

    log("Step 4/4: Scanning indices...")
    active = pick_best_index(broker, model, list(INDEX_CONFIG.keys()))
    cfg    = INDEX_CONFIG[active]

    is_open, status_msg = market_status()
    ist = _ist_now()

    log("-" * 60)
    log(f"  Index      : {cfg['label']}  (lot size: {cfg['lot_size']})")
    log(f"  Stop Loss  : Rs{STOP_LOSS:,}")
    log(f"  Target     : Rs{TARGET:,}")
    log(f"  Delta      : {TARGET_DELTA}")
    log(f"  Spread     : {SPREAD_WIDTH} pts")
    log(f"  Mode       : {'DRY RUN (paper trading)' if SETTINGS.dry_run else 'LIVE TRADING'}")
    log(f"  IST Time   : {ist.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  Market     : {status_msg}")
    log(f"  Hours      : {SETTINGS.market_open_time.strftime('%H:%M')} - "
        f"{SETTINGS.market_close_time.strftime('%H:%M')} IST  (Mon-Fri)")
    log(f"  Dashboard  : http://localhost:8501")
    log("-" * 60)
    log("Regime change rules:")
    for strat, bad in _STRATEGY_INCOMPATIBLE_REGIMES.items():
        good = set(_REGIME_TO_STRATEGY.keys()) - bad
        log(f"  {strat:<14} stays open if regime in {good}  |  closes if regime in {bad}")
    log("-" * 60)

    engine = PaperEngine()
    trade_count[active]     = 0
    last_trade_time[active] = None

    log("Bot running. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                await run_cycle(broker, model, engine, active)
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
