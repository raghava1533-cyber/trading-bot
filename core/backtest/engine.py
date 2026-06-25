from __future__ import annotations
import argparse, json, logging, math, os
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any
import pandas as pd
from scipy.stats import norm
from config import INDEX_CONFIG, SETTINGS
from data.candles import fetch_candles
from ml.features import compute_features
from ml.regime_xgb import load_model, predict_regime
from strategy.strike_selector import select_strikes
from backtest.option_data import HistoricalOptionChainProvider

@dataclass(frozen=True)
class BacktestConfig:
    index: str; initial_capital: float; days: int; interval: str
    entry_every_bars: int; holding_bars: int; commission_per_order: float
    slippage_bps: float; strike_step: int
    chain_width_points: int   # synthetic chain half-width
    spread_width: int         # leg spread distance
    stop_loss: float; target_profit: float; target_delta: float
    risk_free_rate: float; default_iv: float
    option_data_path: str; option_data_format: str
    option_timestamp_tolerance_minutes: int; allow_synthetic_fallback: bool

@dataclass
class BacktestTrade:
    entry_time: str; exit_time: str; index: str; strategy: str; regime: str
    entry_spot: float; exit_spot: float; gross_pnl: float; costs: float
    net_pnl: float; return_pct: float; exit_reason: str; chain_source: str
    legs: list

@dataclass
class BacktestResult:
    config: dict; metrics: dict; equity_curve: list; trades: list

def _bs_price(S, K, T, r, sigma, opt_type):
    if S<=0 or K<=0: return 0.0
    if T<=0 or sigma<=0:
        return float(max(S-K,0) if opt_type=="CE" else max(K-S,0))
    d1 = (math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)
    if opt_type=="CE":
        p = S*norm.cdf(d1)-K*math.exp(-r*T)*norm.cdf(d2)
    else:
        p = K*math.exp(-r*T)*norm.cdf(-d2)-S*norm.cdf(-d1)
    return round(max(float(p),0.05),2)

def _round_to_step(v, step): return int(round(v/step)*step)

def build_synthetic_chain(spot, cfg, timestamp):
    atm   = _round_to_step(spot, cfg.strike_step)
    strikes = range(atm-cfg.chain_width_points, atm+cfg.chain_width_points+cfg.strike_step, cfg.strike_step)
    # Days to Thursday (NIFTY weekly expiry)
    dte = (3 - timestamp.weekday()) % 7
    if dte == 0: dte = 7
    dte = max(dte, int(SETTINGS.min_time_to_expiry_days))
    T   = dte / 365.0
    iv_scale = 0.70 + 0.30 * min(dte/7.0, 1.0)
    iv = cfg.default_iv * iv_scale
    chain = []
    for k in strikes:
        chain.append({"strikePrice":float(k),
            "CE":{"ltp":_bs_price(spot,k,T,cfg.risk_free_rate,iv,"CE"),"iv":iv,
                  "oi":SETTINGS.default_oi,"tradingsymbol":f"{cfg.index}_{k}_CE_BT",
                  "instrument_key":f"BT|{cfg.index}|{k}|CE"},
            "PE":{"ltp":_bs_price(spot,k,T,cfg.risk_free_rate,iv,"PE"),"iv":iv,
                  "oi":SETTINGS.default_oi,"tradingsymbol":f"{cfg.index}_{k}_PE_BT",
                  "instrument_key":f"BT|{cfg.index}|{k}|PE"}})
    return chain

def _strategy_for_regime(r): return {"SIDE":"IRON_CONDOR","BULL":"BULL_PUT","BEAR":"BEAR_CALL"}.get(r)

def _apply_slip(price, side, bps):
    m = 1+bps/10000 if side=="BUY" else 1-bps/10000
    return round(price*m, 2)

def _leg_ltp(chain, strike, opt_type):
    for row in chain:
        if row["strikePrice"]==strike:
            v = row.get(opt_type,{}).get("ltp")
            return float(v) if v is not None else None
    return None

def _position_pnl(legs, chain, bps):
    pnl = 0.0
    for leg in legs:
        ltp = _leg_ltp(chain, leg["strike"], leg["type"])
        if ltp is None: continue
        exit_ltp = _apply_slip(ltp, "SELL" if leg["side"]=="BUY" else "BUY", bps)
        pnl += ((exit_ltp-leg["entry_price"]) if leg["side"]=="BUY"
                else (leg["entry_price"]-exit_ltp)) * leg["qty"]
    return round(pnl, 2)

def _make_legs(raw_legs, lot_size, bps):
    out = []
    for side, strike, opt_type, symbol, ltp, _m in raw_legs:
        out.append({"side":side,"strike":float(strike),"type":opt_type,
                    "symbol":symbol,"entry_price":_apply_slip(float(ltp),side,bps),"qty":lot_size})
    return out

def calculate_metrics(trades, equity_curve, initial_capital):
    if not equity_curve:
        return {"trades":0,"net_pnl":0.0,"final_equity":initial_capital}
    pnls    = [t.net_pnl for t in trades]
    winners = [p for p in pnls if p>0]
    losers  = [p for p in pnls if p<0]
    eq      = pd.Series([p["equity"] for p in equity_curve], dtype="float64")
    peaks   = eq.cummax()
    dds     = eq - peaks
    rets    = eq.pct_change().dropna()
    gp, gl  = sum(winners), abs(sum(losers))
    return {
        "trades":len(trades),"winning_trades":len(winners),"losing_trades":len(losers),
        "win_rate_pct":round(len(winners)/len(trades)*100,2) if trades else 0.0,
        "gross_profit":round(gp,2),"gross_loss":round(gl,2),
        "profit_factor":round(gp/gl,3) if gl else None,
        "net_pnl":round(sum(pnls),2),
        "final_equity":round(float(eq.iloc[-1]),2),
        "return_pct":round((float(eq.iloc[-1])/initial_capital-1)*100,2),
        "max_drawdown":round(float(dds.min()),2),
        "max_drawdown_pct":round(float((dds/peaks).min()*100),2) if not peaks.empty else 0.0,
        "avg_trade":round(sum(pnls)/len(pnls),2) if pnls else 0.0,
        "best_trade":round(max(pnls),2) if pnls else 0.0,
        "worst_trade":round(min(pnls),2) if pnls else 0.0,
        "sharpe_like":round(float(rets.mean()/rets.std()),3) if len(rets)>1 and rets.std() else None,
        "chain_source_counts":pd.Series([t.chain_source for t in trades]).value_counts().to_dict() if trades else {},
    }

def _get_chain(timestamp, spot, config, provider):
    if provider:
        h = provider.get_chain(timestamp, config.index)
        if h: return h, "historical"
        if not config.allow_synthetic_fallback:
            raise RuntimeError(f"No historical chain near {timestamp}")
    return build_synthetic_chain(spot, config, timestamp), "synthetic"

def run_backtest(config, candles=None, model=None, broker=None):
    idx_cfg  = INDEX_CONFIG[config.index]
    lot_size = int(idx_cfg["lot_size"])
    provider = None
    if config.option_data_path:
        try:
            provider = HistoricalOptionChainProvider(config.option_data_path,
                config.option_data_format, config.option_timestamp_tolerance_minutes)
            provider.load()
        except Exception as exc:
            if not config.allow_synthetic_fallback: raise
            logging.warning(f"Historical option data unavailable: {exc}")
    if candles is None:
        candles = fetch_candles(days=config.days, ticker=idx_cfg["yf_ticker"],
                                interval="1d", broker=broker)
    if candles is None or candles.empty:
        raise RuntimeError(f"No candle data for {config.index}")
    candles = compute_features(candles.copy().reset_index(drop=True))
    if candles.empty:
        raise RuntimeError("Not enough candle data after feature calculation")
    logging.info(f"Backtest {config.index}: {len(candles)} bars "
                 f"{candles['timestamp'].iloc[0].date()} -> {candles['timestamp'].iloc[-1].date()}")
    model = model or load_model()
    equity = config.initial_capital
    equity_curve, trades = [], []
    open_pos = None
    last_entry_bar = -config.entry_every_bars

    for bar_idx, row in candles.iterrows():
        ts   = pd.Timestamp(row["timestamp"])
        spot = float(row["close"])
        chain, chain_src = _get_chain(ts, spot, config, provider)
        equity_curve.append({"timestamp":str(ts),"equity":round(equity,2)})

        if open_pos:
            gross = _position_pnl(open_pos["legs"], chain, config.slippage_bps)
            age   = bar_idx - open_pos["entry_bar"]
            sl    = open_pos.get("dynamic_sl",    config.stop_loss)
            tgt   = open_pos.get("dynamic_target", config.target_profit)
            exit_reason = None
            if   gross <= sl:                exit_reason = "STOP_LOSS"
            elif gross >= tgt:               exit_reason = "TARGET"
            elif age  >= config.holding_bars: exit_reason = "HOLDING_PERIOD"
            if exit_reason:
                costs   = len(open_pos["legs"]) * config.commission_per_order * 2
                net_pnl = round(gross - costs, 2)
                equity  = round(equity + net_pnl, 2)
                trades.append(BacktestTrade(
                    entry_time=str(open_pos["entry_time"]), exit_time=str(ts),
                    index=config.index, strategy=open_pos["strategy"],
                    regime=open_pos["regime"], entry_spot=open_pos["entry_spot"],
                    exit_spot=spot, gross_pnl=round(gross,2), costs=round(costs,2),
                    net_pnl=net_pnl, return_pct=round(net_pnl/config.initial_capital*100,4),
                    exit_reason=exit_reason, chain_source=open_pos.get("chain_source","unknown"),
                    legs=open_pos["legs"]))
                equity_curve[-1]["equity"] = equity
                open_pos = None
                last_entry_bar = bar_idx
            else:
                continue

        if bar_idx - last_entry_bar < config.entry_every_bars: continue
        regime   = predict_regime(model, candles.iloc[:bar_idx+1].copy())
        strategy = _strategy_for_regime(regime)
        if not strategy: continue
        dte = max(int(SETTINGS.min_time_to_expiry_days), (3-ts.weekday())%7 or 7)
        T   = max(dte/365.0, SETTINGS.min_time_to_expiry_days/365.0)
        raw_legs = select_strikes(chain, spot, strategy, T=T, r=config.risk_free_rate,
                                  target_delta=config.target_delta, lot_size=lot_size,
                                  spread_width=config.spread_width)
        if not raw_legs:
            logging.debug(f"[{config.index}] {ts.date()} {strategy}: no legs")
            continue
        nc = float((raw_legs[0][5] or {}).get("net_credit", 0) or 0)
        open_pos = {
            "entry_bar":bar_idx,"entry_time":ts,"entry_spot":spot,
            "strategy":strategy,"regime":regime,
            "legs":_make_legs(raw_legs, lot_size, config.slippage_bps),
            "chain_source":chain_src,"net_credit":nc,
            "dynamic_target":round(nc*0.50,2) if nc>0 else config.target_profit,
            "dynamic_sl":    round(-nc*2.0, 2) if nc>0 else config.stop_loss,
        }
        last_entry_bar = bar_idx

    if open_pos:
        row  = candles.iloc[-1]
        ts   = pd.Timestamp(row["timestamp"])
        spot = float(row["close"])
        chain, cs = _get_chain(ts, spot, config, provider)
        gross = _position_pnl(open_pos["legs"], chain, config.slippage_bps)
        costs = len(open_pos["legs"]) * config.commission_per_order * 2
        net   = round(gross-costs, 2)
        equity = round(equity+net, 2)
        trades.append(BacktestTrade(
            entry_time=str(open_pos["entry_time"]),exit_time=str(ts),
            index=config.index,strategy=open_pos["strategy"],regime=open_pos["regime"],
            entry_spot=open_pos["entry_spot"],exit_spot=spot,
            gross_pnl=round(gross,2),costs=round(costs,2),net_pnl=net,
            return_pct=round(net/config.initial_capital*100,4),
            exit_reason="END_OF_DATA",chain_source=open_pos.get("chain_source",cs),
            legs=open_pos["legs"]))
        if equity_curve: equity_curve[-1]["equity"] = equity

    metrics = calculate_metrics(trades, equity_curve, config.initial_capital)
    return BacktestResult(config=asdict(config), metrics=metrics,
                          equity_curve=equity_curve, trades=[asdict(t) for t in trades])

def default_backtest_config(index, years=5):
    index = index.upper()
    if index not in INDEX_CONFIG:
        raise ValueError(f"Unsupported index {index}")
    return BacktestConfig(
        index=index, initial_capital=SETTINGS.backtest_initial_capital,
        days=years*365, interval="1d",
        entry_every_bars=5, holding_bars=10,
        commission_per_order=SETTINGS.backtest_commission_per_order,
        slippage_bps=SETTINGS.backtest_slippage_bps,
        strike_step=SETTINGS.backtest_strike_step,
        chain_width_points=SETTINGS.backtest_chain_width_points,
        spread_width=SETTINGS.spread_width_points,
        stop_loss=SETTINGS.stop_loss, target_profit=SETTINGS.target_profit,
        target_delta=SETTINGS.target_delta, risk_free_rate=SETTINGS.risk_free_rate,
        default_iv=SETTINGS.default_iv,
        option_data_path=SETTINGS.backtest_option_data_path,
        option_data_format=SETTINGS.backtest_option_data_format,
        option_timestamp_tolerance_minutes=SETTINGS.backtest_option_timestamp_tolerance_minutes,
        allow_synthetic_fallback=True)

def save_result(result, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    stamp = date.today().isoformat()
    base  = f"{result.config['index']}_{stamp}"
    jp = os.path.join(output_dir, f"{base}_result.json")
    tp = os.path.join(output_dir, f"{base}_trades.csv")
    ep = os.path.join(output_dir, f"{base}_equity.csv")
    with open(jp,"w",encoding="utf-8") as f: json.dump(asdict(result),f,indent=2)
    pd.DataFrame(result.trades).to_csv(tp, index=False)
    pd.DataFrame(result.equity_curve).to_csv(ep, index=False)
    return {"json":jp,"trades":tp,"equity":ep}
