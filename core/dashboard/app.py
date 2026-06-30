"""
dashboard/app.py  -  Algo Trading Bot Live Dashboard
Shows: open positions, leg details, max profit/loss, today trades, overall P&L
"""
import ast, json, logging, math, os, sys, tempfile, time
from datetime import datetime, date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.dirname(_HERE)
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from config import SETTINGS, INDEX_CONFIG

STATE_FILE = os.path.join(tempfile.gettempdir(), "trading_bot_state.json")

st.set_page_config(page_title="Algo Trading Bot", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# ── Auto-refresh on Live page ─────────────────────────────────────────────────
if st.session_state.get("_page", "Live Trading") == "Live Trading":
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=5000, key="ar")
    except ImportError:
        pass

# ── Helpers ───────────────────────────────────────────────────────────────────
def _read_state() -> dict:
    try:
        import redis as _r
        rc = _r.Redis.from_url(SETTINGS.redis_url, decode_responses=True,
                               socket_connect_timeout=1)
        rc.ping()
        keys = rc.keys("*")
        if keys:
            return {k: v for k, v in zip(keys, rc.mget(keys)) if v}
    except Exception:
        pass
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _parse(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        try:
            return ast.literal_eval(str(raw))
        except Exception:
            return None

def _fmt_pnl(v):
    try:
        f = float(v)
        return f"Rs {f:+,.0f}"
    except Exception:
        return str(v)

def _colour(v):
    try:
        return "color:green;font-weight:bold" if float(v) >= 0 else "color:red;font-weight:bold"
    except Exception:
        return ""

def _write_close_command(index: str):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        cmds = data.get("close_commands", [])
        if isinstance(cmds, str):
            try:
                cmds = json.loads(cmds)
            except Exception:
                cmds = []
        cmds.append({"index": index, "action": "close_all"})
        data["close_commands"] = cmds
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        st.error(f"Failed to write close command: {e}")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 Algo Bot")
    page = st.radio("Navigate",
                    ["Live Trading", "Backtest", "History & Accuracy"],
                    key="_page")
    st.divider()
    st.caption("Settings from .env")
    st.write(f"**Indices:** {', '.join(SETTINGS.active_indices)}")
    st.write(f"**Stop Loss:** Rs {SETTINGS.stop_loss:,}")
    st.write(f"**Target:** Rs {SETTINGS.target_profit:,}")
    st.write(f"**Delta:** {SETTINGS.target_delta}")
    st.write(f"**Mode:** {'DRY RUN' if SETTINGS.dry_run else 'LIVE'}")
    st.divider()
    if st.button("Refresh Now"):
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — LIVE TRADING
# ══════════════════════════════════════════════════════════════════════════════
if page == "Live Trading":
    st.title("Live Trading Dashboard")

    # ── State file check ──────────────────────────────────────────────────────
    if not os.path.exists(STATE_FILE):
        st.error("Bot not running. Start it:  python core/main_async.py")
        st.stop()

    age = time.time() - os.path.getmtime(STATE_FILE)
    if age > 120:
        st.warning(f"State file is {age:.0f}s old — bot may not be running")
    else:
        st.caption(f"Last update: {age:.0f}s ago  |  {STATE_FILE}")

    state = _read_state()
    if not state:
        st.warning("Waiting for first bot cycle...")
        st.stop()

    active_indices = list(SETTINGS.active_indices)

    # ── Per-index summary cards ───────────────────────────────────────────────
    st.subheader("Index Summary")
    cols = st.columns(max(len(active_indices), 1))
    total_unrealized = total_realized = total_today = 0.0

    for i, idx in enumerate(active_indices):
        cfg    = INDEX_CONFIG.get(idx, {})
        spot   = state.get(f"spot_{idx}")
        regime = state.get(f"regime_{idx}", "--")
        pnl    = _parse(state.get(f"pnl_{idx}")) or {}

        unrealized    = float(pnl.get("unrealized",     0))
        realized      = float(pnl.get("realized",       0))
        total         = float(pnl.get("total",          0))
        open_pos      = int(  pnl.get("open_positions", 0))
        today_pnl     = float(pnl.get("today_realized", 0))
        today_trades  = int(  pnl.get("today_trades",   0))
        max_profit    = float(pnl.get("max_profit",     0))
        max_loss      = float(pnl.get("max_loss",       0))
        net_credit    = float(pnl.get("net_credit",     0))

        total_unrealized += unrealized
        total_realized   += realized
        total_today      += today_pnl

        reg_icon = {"BULL": "🟢", "BEAR": "🔴", "SIDE": "🟡"}.get(str(regime), "⚪")

        with cols[i]:
            st.markdown(f"#### {cfg.get('label', idx)}")
            r1c1, r1c2 = st.columns(2)
            r1c1.metric("Spot",   f"Rs {float(spot):,.1f}" if spot else "--")
            r1c2.metric("Regime", f"{reg_icon} {regime}")

            r2c1, r2c2 = st.columns(2)
            r2c1.metric("Open Positions", open_pos)
            r2c2.metric("Today Trades",   today_trades)

            r3c1, r3c2 = st.columns(2)
            r3c1.metric("Unrealized P&L", _fmt_pnl(unrealized),
                        delta=_fmt_pnl(unrealized))
            r3c2.metric("Today Realized", _fmt_pnl(today_pnl),
                        delta=_fmt_pnl(today_pnl))

            if open_pos > 0:
                r4c1, r4c2 = st.columns(2)
                r4c1.metric("Max Profit",  f"Rs {max_profit:,.0f}")
                r4c2.metric("Max Loss",    f"Rs {max_loss:,.0f}")
                r5c1, r5c2 = st.columns(2)
                r5c1.metric("Net Credit",  f"Rs {net_credit:,.0f}")
                rr = f"1:{max_profit/max_loss:.2f}" if max_loss > 0 else "--"
                r5c2.metric("Risk/Reward", rr)

    # ── Combined totals ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Overall P&L Summary")
    tc1, tc2, tc3, tc4 = st.columns(4)
    tc1.metric("Unrealized (Open)",  _fmt_pnl(total_unrealized),
               delta=_fmt_pnl(total_unrealized))
    tc2.metric("Today Realized",     _fmt_pnl(total_today),
               delta=_fmt_pnl(total_today))
    tc3.metric("Session Realized",   _fmt_pnl(total_realized))
    tc4.metric("Session Total",      _fmt_pnl(total_realized + total_unrealized))

    # ── Open Positions detail ─────────────────────────────────────────────────
    st.divider()
    st.subheader("Open Positions")

    all_pos_raw = _parse(state.get("all_positions")) or {}
    leg_rows    = []
    pos_rows    = []

    for idx, positions in all_pos_raw.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if not pos.get("open"):
                continue
            strategy   = pos.get("strategy", "--")
            entry_time = pos.get("entry_time", "--")
            unrealized = float(pos.get("unrealized", 0))
            max_profit = float(pos.get("max_profit", 0))
            max_loss   = float(pos.get("max_loss",   0))
            net_credit = float(pos.get("net_credit", 0))
            m          = pos.get("margin_info", {}) or {}
            margin_req = float(m.get("margin_required", max_loss))
            peak_margin= float(m.get("peak_margin_est", 0))
            rr         = f"1:{max_profit/max_loss:.2f}" if max_loss > 0 else "--"

            pos_rows.append({
                "Index":        idx,
                "Strategy":     strategy,
                "Entry Time":   entry_time,
                "Unrealized":   unrealized,
                "Max Profit":   max_profit,
                "Max Loss":     max_loss,
                "Net Credit":   net_credit,
                "Risk/Reward":  rr,
                "Margin Req":   margin_req,
                "Peak Margin":  peak_margin,
            })

            for leg in pos.get("legs", []):
                entry = float(leg.get("price",          0))
                ltp   = float(leg.get("ltp",            0))
                lpnl  = float(leg.get("unrealized_pnl", 0))
                qty   = int(  leg.get("qty",            75))
                leg_rows.append({
                    "Index":    idx,
                    "Strategy": strategy,
                    "Side":     leg.get("side",   "--"),
                    "Type":     leg.get("type",   "--"),
                    "Strike":   float(leg.get("strike", 0)),
                    "Entry":    entry,
                    "LTP":      ltp,
                    "Change":   round(ltp - entry, 2) if ltp > 0 else 0,
                    "Qty":      qty,
                    "Leg P&L":  lpnl,
                    "Symbol":   leg.get("symbol", "--"),
                })

    if pos_rows:
        # Position-level table
        df_pos = pd.DataFrame(pos_rows)
        st.markdown("**Position Summary**")
        disp_pos = df_pos.copy()
        for col in ["Unrealized", "Max Profit", "Max Loss", "Net Credit",
                    "Margin Req", "Peak Margin"]:
            disp_pos[col] = disp_pos[col].map(lambda x: f"Rs {x:+,.0f}")
        st.dataframe(disp_pos, use_container_width=True, hide_index=True)

        # Leg-level table
        if leg_rows:
            st.markdown("**Leg Details**")
            df_leg = pd.DataFrame(leg_rows)
            disp_leg = df_leg.copy()
            disp_leg["Strike"] = disp_leg["Strike"].map(lambda x: f"Rs {x:,.0f}")
            disp_leg["Entry"]  = disp_leg["Entry"].map(lambda x: f"Rs {x:.2f}")
            disp_leg["LTP"]    = disp_leg["LTP"].map(
                lambda x: f"Rs {x:.2f}" if x > 0 else "--")
            disp_leg["Change"] = disp_leg["Change"].map(
                lambda x: f"Rs {x:+.2f}" if x != 0 else "--")
            disp_leg["Leg P&L"] = disp_leg["Leg P&L"].map(
                lambda x: f"Rs {x:+,.0f}")
            st.dataframe(disp_leg, use_container_width=True, hide_index=True)

        # Combined risk summary
        st.divider()
        st.subheader("Combined Risk Summary")
        total_mp  = sum(r["Max Profit"] for r in pos_rows
                        if isinstance(r["Max Profit"], (int, float)))
        total_ml  = sum(r["Max Loss"]   for r in pos_rows
                        if isinstance(r["Max Loss"],   (int, float)))
        total_nc  = sum(r["Net Credit"] for r in pos_rows
                        if isinstance(r["Net Credit"], (int, float)))
        total_mr  = sum(r["Margin Req"] for r in pos_rows
                        if isinstance(r["Margin Req"], (int, float)))

        rk1, rk2, rk3, rk4 = st.columns(4)
        rk1.metric("Total Max Profit",  f"Rs {total_mp:,.0f}")
        rk2.metric("Total Max Loss",    f"Rs {total_ml:,.0f}")
        rk3.metric("Total Net Credit",  f"Rs {total_nc:,.0f}")
        rk4.metric("Risk/Reward",
                   f"1:{total_mp/total_ml:.2f}" if total_ml > 0 else "--")
        rk5, rk6 = st.columns(2)
        rk5.metric("Margin Required",   f"Rs {total_mr:,.0f}")
        rk6.metric("Unrealized P&L",    _fmt_pnl(total_unrealized))

        # Manual close buttons
        st.divider()
        st.subheader("Manual Close")
        open_indices = [
            idx for idx, positions in all_pos_raw.items()
            if isinstance(positions, list) and any(p.get("open") for p in positions)
        ]
        if open_indices:
            btn_cols = st.columns(len(open_indices) + (1 if len(open_indices) > 1 else 0))
            for i, idx in enumerate(open_indices):
                label  = INDEX_CONFIG.get(idx, {}).get("label", idx)
                pnl_v  = _parse(state.get(f"pnl_{idx}")) or {}
                unreal = float(pnl_v.get("unrealized", 0))
                if btn_cols[i].button(
                    f"Close {label}  (Rs {unreal:+,.0f})",
                    type="primary", use_container_width=True, key=f"close_{idx}"
                ):
                    _write_close_command(idx)
                    st.success(f"Close command sent for {label}")
                    time.sleep(1)
                    st.rerun()
            if len(open_indices) > 1:
                if btn_cols[-1].button("Close ALL", type="primary",
                                       use_container_width=True, key="close_all"):
                    for idx in open_indices:
                        _write_close_command(idx)
                    st.success("Close command sent for all indices")
                    time.sleep(1)
                    st.rerun()
    else:
        st.info("No open positions. Bot is monitoring the market.")

    # ── Today's closed trades ─────────────────────────────────────────────────
    st.divider()
    st.subheader("Today's Closed Trades")
    try:
        from execution.paper_engine import load_trade_history
        history = load_trade_history()
        today_str = date.today().isoformat()
        today_trades = [
            h for h in history
            if str(h.get("exit_time", ""))[:10] == today_str
        ]
        if today_trades:
            df_today = pd.DataFrame(today_trades)
            cols_show = [c for c in ["index", "strategy", "entry_time", "exit_time",
                                     "exit_reason", "net_credit", "max_profit",
                                     "max_loss", "pnl"] if c in df_today.columns]
            disp_today = df_today[cols_show].copy()
            if "pnl" in disp_today.columns:
                disp_today["pnl"] = disp_today["pnl"].map(lambda x: f"Rs {float(x):+,.0f}")
            if "net_credit" in disp_today.columns:
                disp_today["net_credit"] = disp_today["net_credit"].map(
                    lambda x: f"Rs {float(x):,.0f}")
            if "max_profit" in disp_today.columns:
                disp_today["max_profit"] = disp_today["max_profit"].map(
                    lambda x: f"Rs {float(x):,.0f}")
            if "max_loss" in disp_today.columns:
                disp_today["max_loss"] = disp_today["max_loss"].map(
                    lambda x: f"Rs {float(x):,.0f}")
            st.dataframe(disp_today, use_container_width=True, hide_index=True)

            today_pnl_total = sum(float(h.get("pnl", 0)) for h in today_trades)
            winners = [h for h in today_trades if float(h.get("pnl", 0)) > 0]
            losers  = [h for h in today_trades if float(h.get("pnl", 0)) < 0]
            td1, td2, td3, td4 = st.columns(4)
            td1.metric("Trades Today",  len(today_trades))
            td2.metric("Winners",       len(winners))
            td3.metric("Losers",        len(losers))
            td4.metric("Today Net P&L", _fmt_pnl(today_pnl_total),
                       delta=_fmt_pnl(today_pnl_total))
        else:
            st.info("No trades closed today yet.")
    except Exception as e:
        st.warning(f"Could not load today's trades: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Backtest":
    st.title("Strategy Backtest")
    st.info("Uses same XGBoost model, same strategy/delta/spread/SL/target from .env")

    with st.expander("Backtest Settings", expanded=True):
        bc1, bc2, bc3 = st.columns(3)
        bt_index   = bc1.selectbox("Index", list(INDEX_CONFIG.keys()), index=0)
        bt_years   = bc2.slider("Years of data", 1, 5, 5)
        bt_capital = bc3.number_input("Initial Capital (Rs)", value=500000, step=50000)

        st.markdown("**Live strategy params (from .env):**")
        lp1, lp2, lp3, lp4 = st.columns(4)
        lp1.metric("Stop Loss",    f"Rs {SETTINGS.stop_loss:,.0f}")
        lp2.metric("Target",       f"Rs {SETTINGS.target_profit:,.0f}")
        lp3.metric("Delta",        str(SETTINGS.target_delta))
        lp4.metric("Spread Width", f"{SETTINGS.spread_width_points} pts")

        bp1, bp2, bp3 = st.columns(3)
        bt_entry_bars = bp1.number_input("Entry every N bars", value=5,  min_value=1)
        bt_hold_bars  = bp2.number_input("Hold for N bars",    value=10, min_value=1)
        bt_commission = bp3.number_input("Commission/order",   value=20, min_value=0)

    run_bt = st.button("Run Backtest", type="primary", use_container_width=True)

    if run_bt:
        with st.spinner(f"Running {bt_years}-yr backtest on {bt_index}..."):
            try:
                from backtest.engine import run_backtest, BacktestConfig, default_backtest_config
                from dataclasses import asdict
                base_cfg = default_backtest_config(bt_index, years=bt_years)
                cfg = BacktestConfig(**{**asdict(base_cfg),
                    "initial_capital":      float(bt_capital),
                    "entry_every_bars":     int(bt_entry_bars),
                    "holding_bars":         int(bt_hold_bars),
                    "commission_per_order": float(bt_commission)})
                try:
                    from broker.upstox import Broker
                    _broker = Broker()
                except Exception:
                    _broker = None
                result = run_backtest(cfg, broker=_broker)
                st.session_state["bt_result"] = result
                st.session_state["bt_label"]  = f"{bt_index} | {bt_years}yr | Rs{bt_capital:,}"
                st.success("Backtest complete!")
            except Exception as e:
                st.error(f"Backtest failed: {e}")
                import traceback; st.code(traceback.format_exc())

    result = st.session_state.get("bt_result")
    if not result:
        st.info("Configure settings above and click Run Backtest.")
        st.stop()

    st.subheader(f"Results: {st.session_state.get('bt_label','')}")
    m = result.metrics
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Trades",   m.get("trades", 0))
    k2.metric("Win Rate",       f"{m.get('win_rate_pct', 0):.1f}%")
    k3.metric("Net P&L",        f"Rs {m.get('net_pnl', 0):+,.0f}")
    k4.metric("Return",         f"{m.get('return_pct', 0):+.2f}%")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Max Drawdown",   f"Rs {m.get('max_drawdown', 0):,.0f}")
    k6.metric("Profit Factor",  f"{m.get('profit_factor', 0):.2f}" if m.get("profit_factor") else "--")
    k7.metric("Avg Trade",      f"Rs {m.get('avg_trade', 0):+,.0f}")
    k8.metric("Sharpe-like",    f"{m.get('sharpe_like', 0):.3f}" if m.get("sharpe_like") else "--")

    equity_df = pd.DataFrame(result.equity_curve)
    if not equity_df.empty:
        st.subheader("Equity Curve")
        equity_df["timestamp"] = pd.to_datetime(equity_df["timestamp"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=equity_df["timestamp"], y=equity_df["equity"],
                                 mode="lines", name="Equity",
                                 line=dict(color="#00cc88", width=2)))
        fig.add_hline(y=result.config.get("initial_capital", 500000),
                      line_dash="dash", line_color="gray",
                      annotation_text="Initial Capital")
        fig.update_layout(height=350, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    trades_df = pd.DataFrame(result.trades)
    if not trades_df.empty and "entry_time" in trades_df.columns:
        st.subheader("Trade Log")
        st.dataframe(trades_df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — HISTORY & ACCURACY
# ══════════════════════════════════════════════════════════════════════════════
else:
    st.title("Trade History & Accuracy")
    try:
        from execution.paper_engine import load_trade_history
        history = load_trade_history()
    except Exception as e:
        st.error(f"Could not load trade history: {e}")
        history = []

    if not history:
        st.info("No closed trades yet.")
        st.stop()

    df = pd.DataFrame(history)
    pnls    = [float(h.get("pnl", 0)) for h in history]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p < 0]
    net_pnl = sum(pnls)
    win_rate = len(winners) / len(pnls) * 100 if pnls else 0
    pf = round(sum(winners) / abs(sum(losers)), 2) if losers else None

    st.subheader(f"All Closed Trades ({len(df)} total)")
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Total Trades",  len(df))
    h2.metric("Win Rate",      f"{win_rate:.1f}%")
    h3.metric("Net P&L",       _fmt_pnl(net_pnl), delta=_fmt_pnl(net_pnl))
    h4.metric("Profit Factor", f"{pf:.2f}" if pf else "--")
    h5.metric("Avg Trade",     _fmt_pnl(net_pnl / len(pnls)) if pnls else "--")

    # Today summary
    today_str = date.today().isoformat()
    today_h   = [h for h in history if str(h.get("exit_time", ""))[:10] == today_str]
    if today_h:
        st.subheader("Today's Summary")
        t_pnl = sum(float(h.get("pnl", 0)) for h in today_h)
        t_win  = [h for h in today_h if float(h.get("pnl", 0)) > 0]
        td1, td2, td3 = st.columns(3)
        td1.metric("Trades Today", len(today_h))
        td2.metric("Winners Today", len(t_win))
        td3.metric("Today P&L", _fmt_pnl(t_pnl), delta=_fmt_pnl(t_pnl))

    # Monthly P&L chart
    if "entry_time" in df.columns:
        df["entry_dt"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["month"]    = df["entry_dt"].dt.to_period("M").astype(str)
        monthly = df.groupby("month")["pnl"].sum().reset_index()
        monthly.columns = ["Month", "Net P&L"]
        st.subheader("Monthly P&L")
        fig = px.bar(monthly, x="Month", y="Net P&L",
                     color="Net P&L", color_continuous_scale="RdYlGn",
                     color_continuous_midpoint=0)
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # Strategy accuracy
    if "strategy" in df.columns:
        st.subheader("Strategy Accuracy")
        sb = df.groupby("strategy")["pnl"].agg(
            Trades="count",
            Wins=lambda x: (x > 0).sum(),
            Total_PnL="sum",
            Avg_PnL="mean",
        ).reset_index()
        sb["Win Rate"]  = (sb["Wins"] / sb["Trades"] * 100).round(1).astype(str) + "%"
        sb["Total_PnL"] = sb["Total_PnL"].map(lambda x: f"Rs {x:+,.0f}")
        sb["Avg_PnL"]   = sb["Avg_PnL"].map(lambda x: f"Rs {x:+,.0f}")
        st.dataframe(sb, use_container_width=True, hide_index=True)

    # Full trade log
    st.subheader("Full Trade Log")
    cols_show = [c for c in ["index", "strategy", "entry_time", "exit_time",
                              "exit_reason", "net_credit", "max_profit",
                              "max_loss", "pnl"] if c in df.columns]
    disp = df[cols_show].copy()
    if "pnl" in disp.columns:
        disp["pnl"] = disp["pnl"].map(lambda x: f"Rs {float(x):+,.0f}")
    st.dataframe(disp, use_container_width=True, hide_index=True)
