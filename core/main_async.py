"""
main_async.py  -  Trading Bot Entry Point

What happens when you run:  python core/main_async.py
  1. Auto-authenticate  - checks UPSTOX_ACCESS_TOKEN, refreshes if expired
  2. Launch dashboard   - starts Streamlit in background (opens browser)
  3. Train/load model   - XGBoost regime model (retrain if >7 days old)
  4. Scan indices       - picks best index by regime + OI score
  5. Trading loop       - runs every POLL_INTERVAL_SECONDS
"""
import asyncio, datetime, json, logging, os, subprocess, sys, tempfile, traceback
from datetime import date

from data.candles import fetch_candles
from execution.paper_engine import PaperEngine
from greeks.engine import greeks_fd
from broker.upstox import Broker
from infra.redis_bus import set_data
from ml.regime_xgb import load_model, predict_regime
from strategy.strike_selector import select_strikes
from config import SETTINGS, INDEX_CONFIG

# ── Constants from .env ───────────────────────────────────────────────────────
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

# ── Step 1: Auto-authenticate ─────────────────────────────────────────────────
def auto_authenticate() -> bool:
    """
    Check if UPSTOX_ACCESS_TOKEN is valid.
    If expired/missing, run the OAuth2 exchange flow automatically.
    Returns True if authenticated, False if failed.
    """
    import requests, re
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(_env, override=True)

    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

    # ── Check existing token ──────────────────────────────────────────────────
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

    # ── Token missing or expired - try to exchange auth code ─────────────────
    api_key      = os.getenv("UPSTOX_API_KEY", "").strip()
    api_secret   = os.getenv("UPSTOX_API_SECRET", "").strip()
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1").strip()
    auth_code    = os.getenv("UPSTOX_AUTH_CODE", "").strip()

    bad_placeholders = ("", "your_api_key_here", "your_secret_key_here",
                        "paste_auth_code_here", "your_token_here")

    if api_key in bad_placeholders or api_secret in bad_placeholders:
        log("UPSTOX_API_KEY / UPSTOX_API_SECRET not set in .env", logging.ERROR)
        log("Set them and re-run, or run:  python core/broker/auth.py", logging.ERROR)
        return False

    if not auth_code or auth_code in bad_placeholders:
        # No auth code - print login URL and ask user to run auth.py
        from urllib.parse import urlencode
        params = {"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri}
        login_url = f"https://api.upstox.com/v2/login/authorization/dialog?{urlencode(params)}"
        log("Access token expired. Run this to refresh:", logging.WARNING)
        log(f"  python core/broker/auth.py", logging.WARNING)
        log(f"  Or open: {login_url}", logging.WARNING)
        return False

    # ── Exchange auth code for token ──────────────────────────────────────────
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
            log(f"Token exchange failed: {resp.status_code} {resp.text}", logging.ERROR)
            return False
        new_token = resp.json().get("access_token", "")
        if not new_token:
            log("No access_token in response", logging.ERROR)
            return False

        # Save to .env
        content = open(_env, "r", encoding="utf-8").read()
        new_line = f"UPSTOX_ACCESS_TOKEN={new_token}"
        if re.search(r"^UPSTOX_ACCESS_TOKEN=.*$", content, re.MULTILINE):
            content = re.sub(r"^UPSTOX_ACCESS_TOKEN=.*$", new_line, content, re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{new_line}\n"
        # Clear used auth code
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

# ── Step 2: Launch Streamlit dashboard ───────────────────────────────────────
def launch_dashboard() -> subprocess.Popen | None:
    """
    Start Streamlit dashboard in a background process.
    Returns the Popen handle (or None if launch failed).
    """
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
def _write_state(updates: dict):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data.update(updates)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        log(f"_write_state: {exc}", logging.WARNING)

# ── Market hours ──────────────────────────────────────────────────────────────
def market_open() -> bool:
    now = datetime.datetime.now().time()
    return SETTINGS.market_open_time <= now <= SETTINGS.market_close_time

def can_trade(idx: str) -> bool:
    if trade_count.get(idx, 0) >= MAX_TRADES_PER_DAY:
        return False
    lt = last_trade_time.get(idx)
    if lt and (datetime.datetime.now() - lt).seconds < TRADE_COOLDOWN:
        return False
    return True

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_ltp(chain, strike, opt_type):
    for row in chain:
        if row["strikePrice"] == strike:
            return row[opt_type].get("ltp")
    return None

def get_atm_strike(chain, spot):
    return min([r["strikePrice"] for r in chain], key=lambda x: abs(x - spot))

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
                "score": rs * (avg_oi / 100000),
                "regime": regime, "spot": spot, "label": cfg["label"],
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
        last_trade_time[idx] = datetime.datetime.now()
        trade_count[idx]     = trade_count.get(idx, 0) + 1
        log(f"[{idx}] Trade executed: {strategy_name}  "
            f"({'DRY RUN' if SETTINGS.dry_run else 'LIVE'})")
    except Exception:
        log("Trade execution failed", logging.ERROR)
        traceback.print_exc()

# ── Main trading cycle ────────────────────────────────────────────────────────
async def run_cycle(broker, model, engine, idx):
    try:
        if not market_open():
            log(f"[{idx}] Market closed - waiting...")
            return

        cfg      = INDEX_CONFIG[idx]
        lot_size = cfg["lot_size"]

        candles = fetch_candles(ticker=cfg["yf_ticker"], broker=broker)
        if candles is None or candles.empty:
            return

        regime      = predict_regime(model, candles)
        chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
        if not chain:
            return

        engine.mark_to_market(chain)
        pnl         = engine.get_pnl()
        margin_info = engine.get_margin_info()

        # Write state for dashboard
        set_data(f"pnl_{idx}",    str(pnl))
        set_data(f"spot_{idx}",   spot)
        set_data(f"regime_{idx}", regime)
        all_pos = {i: engine.positions for i in SETTINGS.active_indices}
        _write_state({
            f"pnl_{idx}":    str(pnl),
            f"spot_{idx}":   str(spot),
            f"regime_{idx}": regime,
            "all_positions": json.dumps(all_pos, default=str),
        })

        log(f"[{idx}] Spot:{spot:,.0f}  Regime:{regime}  "
            f"Unrealized:{pnl['unrealized']:+,.0f}  Total:{pnl['total']:+,.0f}")

        # ── Exit checks ───────────────────────────────────────────────────────
        if pnl["unrealized"] <= STOP_LOSS:
            log(f"[{idx}] STOP LOSS hit: Rs{pnl['unrealized']:,.0f}", logging.WARNING)
            engine.close_all(exit_reason="STOP_LOSS")
            return

        if pnl["unrealized"] >= TARGET:
            log(f"[{idx}] TARGET hit: Rs{pnl['unrealized']:,.0f}")
            engine.close_all(exit_reason="TARGET")
            return

        # ── Entry checks ──────────────────────────────────────────────────────
        if engine.has_open_positions() or not can_trade(idx):
            return

        # Time to expiry
        expiry_str = broker.get_nearest_expiry(idx)
        if expiry_str:
            try:
                expiry_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d").date()
                T = max((expiry_date - date.today()).days / 365, 1 / 365)
            except Exception:
                T = 0.1
        else:
            T = 0.1

        strategy = {"SIDE": "IRON_CONDOR", "BULL": "BULL_PUT",
                    "BEAR": "BEAR_CALL"}.get(regime)
        if not strategy:
            return

        legs = select_strikes(chain, spot, strategy, T=T,
                              target_delta=TARGET_DELTA,
                              lot_size=lot_size,
                              spread_width=SPREAD_WIDTH)
        if legs:
            await execute_trade(engine, idx, strategy, legs, chain)
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

    # ── Step 1: Authenticate ──────────────────────────────────────────────────
    log("Step 1/4: Checking Upstox authentication...")
    if not auto_authenticate():
        log("Authentication failed. Exiting.", logging.ERROR)
        log("Run:  python core/broker/auth.py  to fix this.", logging.ERROR)
        sys.exit(1)

    # ── Step 2: Launch dashboard ──────────────────────────────────────────────
    log("Step 2/4: Launching dashboard...")
    dash_proc = launch_dashboard()
    if dash_proc:
        log("Dashboard: http://localhost:8501  (open in browser)")
    else:
        log("Dashboard not started - run manually: "
            "streamlit run core/dashboard/app.py", logging.WARNING)

    # ── Step 3: Load model ────────────────────────────────────────────────────
    log("Step 3/4: Loading regime model...")
    broker = Broker()
    model  = load_model(broker=broker)
    log("Model ready")

    # ── Step 4: Pick best index ───────────────────────────────────────────────
    log("Step 4/4: Scanning indices...")
    active = pick_best_index(broker, model, list(INDEX_CONFIG.keys()))
    cfg    = INDEX_CONFIG[active]

    log("-" * 60)
    log(f"  Index:       {cfg['label']}  (lot size: {cfg['lot_size']})")
    log(f"  Stop Loss:   Rs{STOP_LOSS:,}")
    log(f"  Target:      Rs{TARGET:,}")
    log(f"  Delta:       {TARGET_DELTA}")
    log(f"  Spread:      {SPREAD_WIDTH} pts")
    log(f"  Mode:        {'DRY RUN (paper trading)' if SETTINGS.dry_run else '🔴 LIVE TRADING'}")
    log(f"  Dashboard:   http://localhost:8501")
    log("-" * 60)

    engine = PaperEngine()
    trade_count[active]     = 0
    last_trade_time[active] = None

    # ── Trading loop ──────────────────────────────────────────────────────────
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
        # Clean shutdown
        if dash_proc and dash_proc.poll() is None:
            dash_proc.terminate()
            log("Dashboard stopped.")
        log("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot stopped by user.")
