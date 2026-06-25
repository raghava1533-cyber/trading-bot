import asyncio, datetime, json, logging, os, sys, tempfile, traceback
from datetime import date
from execution.paper_engine import PaperEngine
from data.candles import fetch_candles
from ml.regime_xgb import load_model, predict_regime
from greeks.engine import greeks_fd
from broker.upstox import Broker
from infra.redis_bus import set_data
from strategy.strike_selector import select_strikes
from config import SETTINGS, INDEX_CONFIG

POLL_INTERVAL      = SETTINGS.poll_interval_seconds
TRADE_COOLDOWN     = SETTINGS.trade_cooldown_seconds
MAX_TRADES_PER_DAY = SETTINGS.max_trades_per_day
STOP_LOSS          = SETTINGS.stop_loss
TARGET             = SETTINGS.target_profit
TARGET_DELTA       = SETTINGS.target_delta
SPREAD_WIDTH       = SETTINGS.spread_width_points
STATE_FILE = os.path.join(tempfile.gettempdir(), "trading_bot_state.json")

last_trade_time: dict = {}
trade_count:     dict = {}

def setup_logging():
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO,
        handlers=[logging.FileHandler(SETTINGS.log_file, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])

def log(msg, level=logging.INFO): logging.log(level, msg)

def _write_state(updates: dict):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE,"r",encoding="utf-8") as f: data = json.load(f)
        data.update(updates)
        with open(STATE_FILE,"w",encoding="utf-8") as f: json.dump(data, f)
    except Exception as exc:
        log(f"_write_state: {exc}", logging.WARNING)

def market_open():
    now = datetime.datetime.now().time()
    return SETTINGS.market_open_time <= now <= SETTINGS.market_close_time

def can_trade(idx):
    if trade_count.get(idx,0) >= MAX_TRADES_PER_DAY: return False
    lt = last_trade_time.get(idx)
    if lt and (datetime.datetime.now()-lt).seconds < TRADE_COOLDOWN: return False
    return True

def get_ltp(chain, strike, opt_type):
    for row in chain:
        if row["strikePrice"]==strike: return row[opt_type].get("ltp")
    return None

def get_atm_strike(chain, spot):
    return min([r["strikePrice"] for r in chain], key=lambda x: abs(x-spot))

def pick_best_index(broker, model, indices):
    log("Scanning indices...")
    scores = {}
    for idx in indices:
        try:
            cfg     = INDEX_CONFIG[idx]
            candles = fetch_candles(ticker=cfg["yf_ticker"], broker=broker)
            if candles is None or candles.empty: continue
            regime  = predict_regime(model, candles)
            chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
            if not chain or not spot: continue
            atm    = min(chain, key=lambda x: abs(x["strikePrice"]-spot))["strikePrice"]
            nearby = [r for r in chain if abs(r["strikePrice"]-atm)<=300]
            avg_oi = sum((r.get("CE",{}).get("oi",50000)+r.get("PE",{}).get("oi",50000)) for r in nearby)/max(len(nearby),1)
            rs     = {"SIDE":3,"BULL":2,"BEAR":2}.get(regime,1)
            scores[idx] = {"score":rs*(avg_oi/100000),"regime":regime,"spot":spot,"label":cfg["label"]}
            log(f"  {cfg['label']:<14} Spot:{spot:>10,.2f} Regime:{regime:<5} Score:{scores[idx]['score']:.2f}")
        except Exception as e:
            log(f"  {idx}: {e}", logging.ERROR)
    if not scores:
        log("No scores — defaulting to NIFTY", logging.WARNING)
        return "NIFTY"
    best = max(scores, key=lambda x: scores[x]["score"])
    log(f"Best: {scores[best]['label']} (score {scores[best]['score']:.2f})")
    return best

async def execute_trade(engine, idx, strategy_name, legs, chain):
    global last_trade_time, trade_count
    try:
        trade_legs, margin_info = [], None
        for leg in legs:
            side, strike, opt_type, symbol, entry_ltp, margin = leg
            ltp = entry_ltp if entry_ltp is not None else get_ltp(chain, strike, opt_type)
            if ltp is None: log(f"Missing LTP {strike} {opt_type}", logging.ERROR); return
            margin_info = margin
            trade_legs.append({"side":side,"strike":strike,"type":opt_type,
                                "price":ltp,"symbol":symbol,"ltp":ltp,"unrealized_pnl":0})
        engine.add_position(strategy_name, trade_legs, margin_info, index=idx)
        last_trade_time[idx] = datetime.datetime.now()
        trade_count[idx]     = trade_count.get(idx,0)+1
        log(f"[{idx}] Trade executed: {strategy_name}")
    except Exception:
        log("Trade execution failed", logging.ERROR); traceback.print_exc()

async def run_cycle(broker, model, engine, idx):
    try:
        if not market_open(): return
        cfg      = INDEX_CONFIG[idx]
        lot_size = cfg["lot_size"]
        candles  = fetch_candles(ticker=cfg["yf_ticker"], broker=broker)
        if candles is None or candles.empty: return
        regime      = predict_regime(model, candles)
        chain, spot = broker.get_option_chain(idx, range_size=cfg["range_size"])
        if not chain: return
        engine.mark_to_market(chain)
        pnl         = engine.get_pnl()
        margin_info = engine.get_margin_info()
        set_data(f"pnl_{idx}",   str(pnl))
        set_data(f"spot_{idx}",  spot)
        set_data(f"regime_{idx}",regime)
        all_pos = {i: engine.positions for i in SETTINGS.active_indices}
        _write_state({f"pnl_{idx}":str(pnl), f"spot_{idx}":str(spot),
                      f"regime_{idx}":regime, "all_positions":json.dumps(all_pos)})
        log(f"[{idx}] Spot:{spot:.0f} Regime:{regime} PnL:{pnl['total']:+.0f}")
        if pnl["unrealized"] <= STOP_LOSS:
            log(f"[{idx}] Stop loss hit: {pnl['unrealized']:,.0f}", logging.WARNING)
            engine.close_all(exit_reason="STOP_LOSS"); return
        if pnl["unrealized"] >= TARGET:
            log(f"[{idx}] Target hit: {pnl['unrealized']:,.0f}")
            engine.close_all(exit_reason="TARGET"); return
        if engine.has_open_positions() or not can_trade(idx): return
        expiry_str = broker.get_nearest_expiry(idx)
        if expiry_str:
            try:
                expiry_date = datetime.datetime.strptime(expiry_str,"%Y-%m-%d").date()
                T = max((expiry_date-date.today()).days/365, 1/365)
            except Exception: T = 0.1
        else: T = 0.1
        strategy = {"SIDE":"IRON_CONDOR","BULL":"BULL_PUT","BEAR":"BEAR_CALL"}.get(regime)
        if not strategy: return
        legs = select_strikes(chain, spot, strategy, T=T, target_delta=TARGET_DELTA,
                              lot_size=lot_size, spread_width=SPREAD_WIDTH)
        if legs: await execute_trade(engine, idx, strategy, legs, chain)
        else: log(f"[{idx}] {strategy}: no legs returned", logging.WARNING)
    except Exception:
        log(f"[{idx}] Cycle error", logging.ERROR); traceback.print_exc()

async def main():
    setup_logging()
    broker = Broker()
    model  = load_model(broker=broker)
    active = pick_best_index(broker, model, list(INDEX_CONFIG.keys()))
    cfg    = INDEX_CONFIG[active]
    log(f"Selected: {cfg['label']} | SL:{STOP_LOSS} Target:{TARGET} Delta:{TARGET_DELTA} Spread:{SPREAD_WIDTH}pts")
    engine = PaperEngine()
    trade_count[active]     = 0
    last_trade_time[active] = None
    while True:
        try: await run_cycle(broker, model, engine, active)
        except Exception as e: log(f"Loop error: {e}", logging.ERROR); traceback.print_exc()
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log("Bot stopped")
