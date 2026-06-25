"""
dashboard/app.py  —  Algo Trading Bot  |  Live + Backtest Dashboard
Pages:
  1. Live Trading   — real-time positions, P&L, margin
  2. Backtest       — strategy backtest with full analytics
  3. History        — closed trade history and accuracy
"""
import ast, os, sys, time, json, tempfile, math
from datetime import datetime

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.dirname(_HERE)
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from config import SETTINGS, INDEX_CONFIG

STATE_FILE = os.path.join(tempfile.gettempdir(), "trading_bot_state.json")

st.set_page_config(page_title="Algo Trading Bot", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# ── Auto-refresh ONLY on Live Trading page ────────────────────────────────
_current_page = st.session_state.get("_page", "🟢 Live Trading")
if _current_page == "🟢 Live Trading":
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=5000, key="ar")
    except ImportError:
        st.sidebar.warning("⚠️ streamlit-autorefresh not installed — auto-refresh disabled.")

# ── Helpers ───────────────────────────────────────────────────────────────
def _read_state() -> dict:
    try:
        import redis as _redis
        from config import SETTINGS as _S
        _r = _redis.Redis.from_url(_S.redis_url, decode_responses=True, socket_connect_timeout=1)
        _r.ping()
        keys = _r.keys("*")
        if keys:
            vals = _r.mget(keys)
            return {k: v for k, v in zip(keys, vals) if v is not None}
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
    if raw is None: return None
    if isinstance(raw, (dict, list)): return raw
    try: return ast.literal_eval(str(raw))
    except Exception: return None

def _colour_pnl(v):
    try: return "color:green;font-weight:bold" if float(v) >= 0 else "color:red;font-weight:bold"
    except: return ""

def _write_close_command(index: str):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        existing = data.get("close_commands", [])
        if isinstance(existing, str):
            try: existing = ast.literal_eval(existing)
            except: existing = []
        existing.append({"index": index, "action": "close_all"})
        data["close_commands"] = existing
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        st.error(f"Failed to write close command: {e}")

# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 Algo Bot")
    page = st.radio("Navigate",
                    ["🟢 Live Trading", "📊 Backtest", "📜 History & Accuracy"],
                    key="_page")
    st.divider()
    st.write(f"**Indices:** {', '.join(SETTINGS.active_indices)}")
    st.write(f"**Stop Loss:** ₹{SETTINGS.stop_loss:,}")
    st.write(f"**Target:** ₹{SETTINGS.target_profit:,}")
    st.write(f"**Delta:** {SETTINGS.target_delta}")
    st.write(f"**Dry Run:** {'✅ Yes' if SETTINGS.dry_run else '🔴 LIVE'}")
    st.divider()
    if st.button("🔄 Refresh Now"):
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════
# PAGE 1 — LIVE TRADING
# ══════════════════════════════════════════════════════════════════════════
if page == "🟢 Live Trading":
    st.title("🟢 Live Trading Dashboard")
    state = _read_state()
    active_indices = list(SETTINGS.active_indices)

    if os.path.exists(STATE_FILE):
        age = time.time() - os.path.getmtime(STATE_FILE)
        st.caption(f"State file: `{STATE_FILE}`  |  Last update: **{age:.0f}s ago**")
        if age > 120:
            st.warning("⚠️ Bot may not be running — state file is stale (>2 min)")
    else:
        st.error("State file not found — start the bot: `python core/main_async.py`")
        st.stop()

    if not state:
        st.warning("No data yet — waiting for first bot cycle...")
        st.stop()

    st.subheader("Index Summary")
    total_realized = total_unrealized = 0.0
    cols = st.columns(max(len(active_indices), 1))
    for i, idx in enumerate(active_indices):
        cfg    = INDEX_CONFIG.get(idx, {})
        label  = cfg.get("label", idx)
        spot   = state.get(f"spot_{idx}")
        regime = state.get(f"regime_{idx}", "--")
        pnl    = _parse(state.get(f"pnl_{idx}")) or {}
        realized   = float(pnl.get("realized",   0))
        unrealized = float(pnl.get("unrealized", 0))
        total      = float(pnl.get("total",      0))
        open_pos   = int(pnl.get("open_positions", 0))
        total_realized   += realized
        total_unrealized += unrealized
        reg_icon = {"BULL":"🟢","BEAR":"🔴","SIDE":"🟡"}.get(str(regime),"⚪")
        with cols[i]:
            st.markdown(f"#### {label}")
            c1,c2 = st.columns(2)
            c1.metric("Spot",   f"₹{float(spot):,.1f}" if spot else "--")
            c2.metric("Regime", f"{reg_icon} {regime}")
            c3,c4 = st.columns(2)
            c3.metric("Unrealized", f"₹{unrealized:+,.0f}")
            c4.metric("Open Pos",   open_pos)
            c5,c6 = st.columns(2)
            c5.metric("Realized",  f"₹{realized:+,.0f}")
            c6.metric("Total P&L", f"₹{total:+,.0f}")

    st.divider()
    combined = total_realized + total_unrealized
    cc = st.columns(4)
    cc[0].metric("Combined Realized",   f"₹{total_realized:+,.0f}")
    cc[1].metric("Combined Unrealized", f"₹{total_unrealized:+,.0f}")
    cc[2].metric("Combined Total",      f"₹{combined:+,.0f}")
    cc[3].metric("Indices Active",      len(active_indices))

    st.divider()
    st.subheader("Open Positions")
    all_pos = _parse(state.get("all_positions")) or {}
    leg_rows, strat_rows, seen = [], [], set()
    comb_max_profit = comb_max_loss = comb_net_credit = comb_peak = comb_margin_seq = 0.0

    for idx, positions in all_pos.items():
        for pos in positions:
            if not pos.get("open"): continue
            m   = pos.get("margin_info") or {}
            mp  = float(m.get("max_profit",      0))
            ml  = float(m.get("max_loss",        0))
            nc  = float(m.get("net_credit",      0))
            mr  = float(m.get("margin_required", 0))
            pk  = float(m.get("peak_margin_est", 0))
            ms  = float(m.get("margin_sequential", ml*2))
            cps = float(m.get("credit_per_share", 0))
            strat = pos.get("strategy","--")
            key = (idx, strat)
            if key not in seen:
                seen.add(key)
                comb_max_profit += mp; comb_max_loss += ml
                comb_net_credit += nc; comb_peak     += pk; comb_margin_seq += ms
                strat_rows.append({"Index":idx,"Strategy":strat,"Net Credit":nc,
                    "Max Profit":mp,"Max Loss":ml,"Margin (Basket)":mr,
                    "Margin (Sequential)":ms,"Peak Margin (Naked Sell)":pk,"Credit/Share":cps})
            for leg in pos.get("legs", []):
                leg_rows.append({"Index":idx,"Strategy":strat,
                    "Side":leg.get("side","--"),"Type":leg.get("type","--"),
                    "Strike":float(leg.get("strike",0)),"Entry":float(leg.get("price",0)),
                    "LTP":float(leg.get("ltp",0)),"Leg P&L":float(leg.get("unrealized_pnl",0)),
                    "Symbol":leg.get("symbol","--")})

    if leg_rows:
        df = pd.DataFrame(leg_rows)
        df_d = df.copy()
        df_d["Strike"] = df_d["Strike"].map(lambda x: f"₹{x:,.0f}")
        df_d["Entry"]  = df_d["Entry"].map(lambda x: f"₹{x:,.2f}")
        df_d["LTP"]    = df_d["LTP"].map(lambda x: f"₹{x:,.2f}" if x>0 else "--")
        st.dataframe(df_d.style.map(_colour_pnl, subset=["Leg P&L"])
                     .format({"Leg P&L": lambda x: f"₹{x:+,.2f}"}),
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Combined Risk Summary")
        r1 = st.columns(4)
        r1[0].metric("Max Profit",  f"₹{comb_max_profit:,.0f}")
        r1[1].metric("Max Loss",    f"₹{comb_max_loss:,.0f}")
        r1[2].metric("Net Credit",  f"₹{comb_net_credit:,.0f}")
        r1[3].metric("Risk/Reward", f"1:{comb_max_profit/comb_max_loss:.2f}" if comb_max_loss>0 else "--")
        r2 = st.columns(3)
        r2[0].metric("Margin — Basket",     f"₹{comb_max_loss:,.0f}")
        r2[1].metric("Margin — Sequential", f"₹{comb_margin_seq:,.0f}")
        r2[2].metric("Peak Margin",         f"₹{comb_peak:,.0f}")

        if strat_rows:
            st.subheader("Per-Strategy Breakdown")
            df_s = pd.DataFrame(strat_rows)
            for col in ["Net Credit","Max Profit","Max Loss","Margin (Basket)",
                        "Margin (Sequential)","Peak Margin (Naked Sell)","Credit/Share"]:
                df_s[col] = df_s[col].map(lambda x: f"₹{x:,.2f}")
            st.dataframe(df_s, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("🔴 Manual Close Positions")
        st.warning(f"⚠️ Close command sent to bot. Executes on next poll (~{SETTINGS.poll_interval_seconds}s). "
                   f"{'DRY RUN' if SETTINGS.dry_run else '🔴 LIVE MODE'}")
        open_indices = [idx for idx, positions in all_pos.items()
                        if any(p.get("open") for p in positions)]
        if open_indices:
            btn_cols = st.columns(len(open_indices)+(1 if len(open_indices)>1 else 0))
            for i, idx in enumerate(open_indices):
                lbl_i  = INDEX_CONFIG.get(idx,{}).get("label",idx)
                unreal = float((_parse(state.get(f"pnl_{idx}")) or {}).get("unrealized",0))
                if btn_cols[i].button(f"🔴 Close {lbl_i} (₹{unreal:+,.0f})",
                                      type="primary", use_container_width=True, key=f"close_{idx}"):
                    _write_close_command(idx)
                    st.success(f"✅ Close command sent for {lbl_i}.")
                    time.sleep(1); st.rerun()
            if len(open_indices)>1:
                if btn_cols[-1].button("🔴 Close ALL", type="primary",
                                       use_container_width=True, key="close_all"):
                    for idx in open_indices: _write_close_command(idx)
                    st.success("✅ Close command sent for all indices.")
                    time.sleep(1); st.rerun()
        else:
            st.info("No open positions to close.")
    else:
        st.info("No open positions")

# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════
elif page == "📊 Backtest":
    st.title("📊 Strategy Backtest")
    st.info("Backtest uses the same XGBoost regime model, same strategy map "
            "(BULL→BULL_PUT | BEAR→BEAR_CALL | SIDE→IRON_CONDOR), "
            "same delta/spread/lot/SL/target from .env, and a synthetic Black-Scholes chain.")

    with st.expander("⚙️ Backtest Settings", expanded=True):
        bc1, bc2, bc3 = st.columns(3)
        bt_index   = bc1.selectbox("Index", list(INDEX_CONFIG.keys()), index=0)
        bt_years   = bc2.slider("Years of data", 1, 5, 5)
        bt_capital = bc3.number_input("Initial Capital (Rs)", value=500000, step=50000)

        st.markdown("**Live strategy params (from `.env`) — used as-is:**")
        import importlib, config as _cfg_mod
        importlib.reload(_cfg_mod)
        from config import SETTINGS as _S, INDEX_CONFIG as _IC
        lp1, lp2, lp3, lp4, lp5 = st.columns(5)
        lp1.metric("Stop Loss",     f"Rs {_S.stop_loss:,.0f}")
        lp2.metric("Target Profit", f"Rs {_S.target_profit:,.0f}")
        lp3.metric("Target Delta",  str(_S.target_delta))
        lp4.metric("Spread Width",  f"{_S.spread_width_points} pts")
        lp5.metric("Lot Size",      str(_IC.get(bt_index, {}).get("lot_size", 75)))

        st.markdown("**Backtest-specific params:**")
        bp1, bp2, bp3 = st.columns(3)
        bt_entry_bars  = bp1.number_input("Entry every N bars (days)", value=5,  min_value=1, max_value=20)
        bt_hold_bars   = bp2.number_input("Hold for N bars (days)",    value=10, min_value=1, max_value=30)
        bt_commission  = bp3.number_input("Commission per order (Rs)", value=20, min_value=0, max_value=200)

    _bt_running = st.session_state.get("bt_running", False)
    run_bt = st.button("⏳ Running..." if _bt_running else "▶️ Run Backtest",
                       type="primary", use_container_width=True, disabled=_bt_running)

    if run_bt and not _bt_running:
        st.session_state["bt_running"]     = True
        st.session_state["bt_index"]       = bt_index
        st.session_state["bt_years"]       = bt_years
        st.session_state["bt_capital"]     = bt_capital
        st.session_state["bt_entry_bars"]  = bt_entry_bars
        st.session_state["bt_hold_bars"]   = bt_hold_bars
        st.session_state["bt_commission"]  = bt_commission
        st.session_state["bt_result"]      = None
        st.rerun()

    if st.session_state.get("bt_running"):
        _idx  = st.session_state["bt_index"]
        _yrs  = st.session_state["bt_years"]
        _cap  = st.session_state["bt_capital"]
        _eb   = st.session_state["bt_entry_bars"]
        _hb   = st.session_state["bt_hold_bars"]
        _comm = st.session_state["bt_commission"]
        with st.spinner(f"Running {_yrs}-yr backtest on {_idx} — please wait..."):
            try:
                from backtest.engine import run_backtest, BacktestConfig, default_backtest_config
                from dataclasses import asdict
                base_cfg = default_backtest_config(_idx, years=_yrs)
                cfg = BacktestConfig(**{**asdict(base_cfg),
                    "initial_capital":      float(_cap),
                    "entry_every_bars":     int(_eb),
                    "holding_bars":         int(_hb),
                    "commission_per_order": float(_comm)})
                try:
                    from broker.upstox import Broker
                    _broker = Broker()
                except Exception:
                    _broker = None
                result = run_backtest(cfg, broker=_broker)
                st.session_state["bt_result"] = result
                st.session_state["bt_label"]  = f"{_idx} | {_yrs}yr | Rs{_cap:,}"
                st.session_state["bt_running"] = False
                st.success("✅ Backtest complete!")
            except Exception as e:
                st.session_state["bt_running"] = False
                st.error(f"Backtest failed: {e}")
                import traceback; st.code(traceback.format_exc())

    result = st.session_state.get("bt_result")
    if not result:
        st.info("Configure settings above and click **▶️ Run Backtest**.")
        st.stop()

    st.subheader(f"Results: {st.session_state.get('bt_label','')}")
    cfg_used = result.config
    cu1,cu2,cu3,cu4,cu5 = st.columns(5)
    cu1.metric("Index",        cfg_used["index"])
    cu2.metric("Stop Loss",    f"Rs {cfg_used['stop_loss']:,.0f}")
    cu3.metric("Target",       f"Rs {cfg_used['target_profit']:,.0f}")
    cu4.metric("Delta",        cfg_used["target_delta"])
    cu5.metric("Spread Width", f"{cfg_used['spread_width']} pts")

    m = result.metrics
    st.subheader("Key Performance Indicators")
    k1,k2,k3,k4 = st.columns(4)
    k1.metric("Total Trades",   m.get("trades",0))
    k2.metric("Winning Trades", m.get("winning_trades",0), f"+{m.get('winning_trades',0)}")
    k3.metric("Losing Trades",  m.get("losing_trades",0),  f"-{m.get('losing_trades',0)}")
    k4.metric("Win Rate",       f"{m.get('win_rate_pct',0):.1f}%")

    k5,k6,k7,k8 = st.columns(4)
    k5.metric("Prob of Profit", f"{m.get('win_rate_pct',0):.1f}%")
    net = m.get("net_pnl",0)
    k6.metric("Net P&L",        f"₹{net:+,.0f}", f"₹{net:+,.0f}")
    k7.metric("Final Equity",   f"₹{m.get('final_equity',0):,.0f}")
    ret = m.get("return_pct",0)
    k8.metric("Return",         f"{ret:+.2f}%", f"{ret:+.2f}%")

    k9,k10,k11,k12 = st.columns(4)
    dd  = m.get("max_drawdown",0)
    ddp = m.get("max_drawdown_pct",0)
    k9.metric("Max Drawdown",   f"₹{dd:,.0f}", f"{ddp:.1f}%")
    pf  = m.get("profit_factor")
    k10.metric("Profit Factor", f"{pf:.2f}" if pf else "—")
    k11.metric("Avg Trade P&L", f"₹{m.get('avg_trade',0):+,.0f}")
    sh  = m.get("sharpe_like")
    k12.metric("Sharpe-like",   f"{sh:.3f}" if sh else "—")

    k13,k14 = st.columns(2)
    bt = m.get("best_trade",0); wt = m.get("worst_trade",0)
    k13.metric("Best Trade",  f"₹{bt:+,.0f}", f"₹{bt:+,.0f}")
    k14.metric("Worst Trade", f"₹{wt:+,.0f}", f"₹{wt:+,.0f}")

    trades_df = pd.DataFrame(result.trades)
    equity_df = pd.DataFrame(result.equity_curve)

    if not equity_df.empty:
        st.subheader("Equity Curve")
        equity_df["timestamp"] = pd.to_datetime(equity_df["timestamp"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=equity_df["timestamp"], y=equity_df["equity"],
                                 mode="lines", name="Equity",
                                 line=dict(color="#00cc88", width=2)))
        fig.add_hline(y=cfg_used["initial_capital"], line_dash="dash",
                      line_color="gray", annotation_text="Initial Capital")
        fig.update_layout(height=350, margin=dict(l=0,r=0,t=30,b=0),
                          xaxis_title="Date", yaxis_title="Equity (₹)")
        st.plotly_chart(fig, use_container_width=True)

    if not trades_df.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Daily P&L Histogram")
            fig2 = px.histogram(trades_df, x="net_pnl", nbins=30,
                                color_discrete_sequence=["#636EFA"])
            fig2.add_vline(x=0, line_dash="dash", line_color="red")
            fig2.update_layout(height=300, margin=dict(l=0,r=0,t=30,b=0),
                               xaxis_title="Net P&L (₹)", yaxis_title="Count")
            st.plotly_chart(fig2, use_container_width=True)
        with c2:
            st.subheader("Trade P&L Distribution")
            fig3 = px.box(trades_df, y="net_pnl", color_discrete_sequence=["#EF553B"])
            fig3.add_hline(y=0, line_dash="dash", line_color="gray")
            fig3.update_layout(height=300, margin=dict(l=0,r=0,t=30,b=0))
            st.plotly_chart(fig3, use_container_width=True)

        if "strategy" in trades_df.columns:
            c3, c4 = st.columns(2)
            with c3:
                st.subheader("Strategy Breakdown")
                sb = trades_df.groupby("strategy")["net_pnl"].agg(["count","sum","mean"]).reset_index()
                sb.columns = ["Strategy","Trades","Total P&L","Avg P&L"]
                st.dataframe(sb.style.format({"Total P&L":"₹{:+,.0f}","Avg P&L":"₹{:+,.0f}"}),
                             use_container_width=True, hide_index=True)
            with c4:
                st.subheader("Regime Breakdown")
                rb = trades_df.groupby("regime")["net_pnl"].agg(["count","sum","mean"]).reset_index()
                rb.columns = ["Regime","Trades","Total P&L","Avg P&L"]
                st.dataframe(rb.style.format({"Total P&L":"₹{:+,.0f}","Avg P&L":"₹{:+,.0f}"}),
                             use_container_width=True, hide_index=True)

        if "entry_time" in trades_df.columns:
            st.subheader("Monthly P&L Heatmap")
            try:
                trades_df["entry_dt"] = pd.to_datetime(trades_df["entry_time"])
                trades_df["month"]    = trades_df["entry_dt"].dt.to_period("M").astype(str)
                monthly = trades_df.groupby("month")["net_pnl"].sum().reset_index()
                monthly.columns = ["Month","Net P&L"]
                fig4 = px.bar(monthly, x="Month", y="Net P&L",
                              color="Net P&L", color_continuous_scale="RdYlGn",
                              color_continuous_midpoint=0)
                fig4.update_layout(height=300, margin=dict(l=0,r=0,t=30,b=0))
                st.plotly_chart(fig4, use_container_width=True)
            except Exception: pass

        st.subheader("Trade Log")
        disp = trades_df[["entry_time","exit_time","strategy","regime",
                           "entry_spot","exit_spot","gross_pnl","costs",
                           "net_pnl","exit_reason"]].copy()
        disp["entry_spot"] = disp["entry_spot"].map(lambda x: f"₹{x:,.0f}")
        disp["exit_spot"]  = disp["exit_spot"].map(lambda x: f"₹{x:,.0f}")
        disp["gross_pnl"]  = disp["gross_pnl"].map(lambda x: f"₹{x:+,.0f}")
        disp["costs"]      = disp["costs"].map(lambda x: f"₹{x:,.0f}")
        st.dataframe(disp.style.map(_colour_pnl, subset=["net_pnl"])
                     .format({"net_pnl": lambda x: f"₹{x:+,.0f}"}),
                     use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════
# PAGE 3 — HISTORY & ACCURACY
# ══════════════════════════════════════════════════════════════════════════
else:  # History & Accuracy
    st.title("📜 Trade History & Accuracy")
    try:
        from execution.paper_engine import load_trade_history
        history = load_trade_history()
    except Exception as e:
        st.error(f"Could not load trade history: {e}")
        history = []

    if not history:
        st.info("No closed trades yet. Trades appear here after the bot closes positions.")
        st.stop()

    df = pd.DataFrame(history)
    st.subheader(f"Closed Trades ({len(df)} total)")

    pnls    = df["pnl"].tolist()
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p < 0]
    win_rate = len(winners)/len(pnls)*100 if pnls else 0
    net_pnl  = sum(pnls)
    gp, gl   = sum(winners), abs(sum(losers))
    pf       = round(gp/gl, 2) if gl else None

    h1,h2,h3,h4,h5 = st.columns(5)
    h1.metric("Total Trades",  len(df))
    h2.metric("Win Rate",      f"{win_rate:.1f}%")
    h3.metric("Net P&L",       f"₹{net_pnl:+,.0f}")
    h4.metric("Profit Factor", f"{pf:.2f}" if pf else "—")
    h5.metric("Avg Trade",     f"₹{net_pnl/len(pnls):+,.0f}" if pnls else "—")

    if "entry_time" in df.columns:
        df["entry_dt"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["month"]    = df["entry_dt"].dt.to_period("M").astype(str)
        st.subheader("Monthly P&L")
        monthly = df.groupby("month")["pnl"].sum().reset_index()
        monthly.columns = ["Month","Net P&L"]
        fig = px.bar(monthly, x="Month", y="Net P&L",
                     color="Net P&L", color_continuous_scale="RdYlGn",
                     color_continuous_midpoint=0)
        fig.update_layout(height=300, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig, use_container_width=True)

    if "strategy" in df.columns:
        st.subheader("Strategy Accuracy")
        sb = df.groupby("strategy")["pnl"].agg(
            Trades="count",
            Wins=lambda x: (x>0).sum(),
            Total_PnL="sum",
            Avg_PnL="mean"
        ).reset_index()
        sb["Win Rate"] = (sb["Wins"]/sb["Trades"]*100).round(1).astype(str)+"%"
        sb["Total_PnL"] = sb["Total_PnL"].map(lambda x: f"₹{x:+,.0f}")
        sb["Avg_PnL"]   = sb["Avg_PnL"].map(lambda x: f"₹{x:+,.0f}")
        st.dataframe(sb, use_container_width=True, hide_index=True)

    st.subheader("Trade Log")
    disp_cols = [c for c in ["index","strategy","entry_time","exit_time",
                              "exit_reason","pnl","carried_over"] if c in df.columns]
    disp = df[disp_cols].copy()
    st.dataframe(disp.style.map(_colour_pnl, subset=["pnl"] if "pnl" in disp.columns else [])
                 .format({"pnl": lambda x: f"₹{x:+,.0f}"} if "pnl" in disp.columns else {}),
                 use_container_width=True, hide_index=True)
