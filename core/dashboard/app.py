"""
dashboard/app.py  -  Algo Trading Bot Live Dashboard
Shows: open positions, leg details, max profit/loss, R:R, credit ratio, today trades
"""
import ast, json, os, sys, tempfile, time
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

# Auto-refresh on Live page
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
    if raw is None: return None
    if isinstance(raw, (dict, list)): return raw
    try: return json.loads(str(raw))
    except Exception:
        try: return ast.literal_eval(str(raw))
        except Exception: return None

def _rs(v, sign=False):
    try:
        f = float(v)
        s = "+" if sign and f > 0 else ""
        return f"₹{s}{f:,.0f}"
    except Exception: return str(v)

def _pct(v):
    try: return f"{float(v)*100:.1f}%"
    except Exception: return "--"

def _rr_color(rr):
    """Return streamlit color string for R:R ratio."""
    try:
        r = float(rr)
        if r >= 0.33: return "normal"   # >= 1:3 good
        if r >= 0.20: return "off"      # 1:5 marginal
        return "inverse"                # < 1:5 bad
    except Exception: return "off"

def _write_close_command(index: str, action: str = "close_all", pos_idx: int = -1):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        cmds = data.get("close_commands", [])
        if isinstance(cmds, str):
            try: cmds = json.loads(cmds)
            except Exception: cmds = []
        cmds.append({"index": index, "action": action, "position_index": pos_idx})
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
    st.caption("Settings (.env)")
    st.write(f"**Indices:** {', '.join(SETTINGS.active_indices)}")
    st.write(f"**Stop Loss:** ₹{SETTINGS.stop_loss:,}")
    st.write(f"**Target:** ₹{SETTINGS.target_profit:,}")
    st.write(f"**Delta:** {SETTINGS.target_delta}")
    st.write(f"**Spread:** {SETTINGS.spread_width_points} pts")
    st.write(f"**Min Credit Ratio:** {SETTINGS.min_credit_ratio:.0%}")
    st.write(f"**Mode:** {'🟡 DRY RUN' if SETTINGS.dry_run else '🔴 LIVE'}")
    st.divider()
    st.caption("💡 R:R Guide")
    st.write("🟢 ≥ 1:3  (credit ≥ 33%)")
    st.write("🟡 1:5  (credit ≥ 20%)")
    st.write("🔴 < 1:5  (credit < 20%)")
    st.divider()
    if st.button("🔄 Refresh Now"):
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — LIVE TRADING
# ══════════════════════════════════════════════════════════════════════════════
if page == "Live Trading":
    st.title("🟢 Live Trading Dashboard")

    if not os.path.exists(STATE_FILE):
        st.error("Bot not running. Start:  `python core/main_async.py`")
        st.stop()

    age = time.time() - os.path.getmtime(STATE_FILE)
    col_hdr1, col_hdr2 = st.columns([3, 1])
    with col_hdr1:
        if age > 120:
            st.warning(f"⚠️ State file is {age:.0f}s old — bot may not be running")
        else:
            st.success(f"✅ Live — last update {age:.0f}s ago")
    with col_hdr2:
        st.caption(STATE_FILE)

    state = _read_state()
    if not state:
        st.warning("Waiting for first bot cycle...")
        st.stop()

    active_indices = list(SETTINGS.active_indices)

    # ── Overall P&L bar ───────────────────────────────────────────────────────
    total_unrealized = total_today = total_realized = 0.0
    for idx in active_indices:
        pnl = _parse(state.get(f"pnl_{idx}")) or {}
        total_unrealized += float(pnl.get("unrealized",     0))
        total_today      += float(pnl.get("today_realized", 0))
        total_realized   += float(pnl.get("realized",       0))

    st.subheader("Overall P&L")
    oc1, oc2, oc3, oc4 = st.columns(4)
    oc1.metric("Unrealized (Open)",  _rs(total_unrealized, True),
               delta=_rs(total_unrealized, True))
    oc2.metric("Today Realized",     _rs(total_today, True),
               delta=_rs(total_today, True))
    oc3.metric("Session Realized",   _rs(total_realized, True))
    oc4.metric("Session Total",      _rs(total_realized + total_unrealized, True))

    st.divider()

    # ── Per-index cards ───────────────────────────────────────────────────────
    st.subheader("Index Summary")
    cols = st.columns(max(len(active_indices), 1))
    for i, idx in enumerate(active_indices):
        cfg    = INDEX_CONFIG.get(idx, {})
        spot   = state.get(f"spot_{idx}")
        regime = state.get(f"regime_{idx}", "--")
        pnl    = _parse(state.get(f"pnl_{idx}")) or {}

        unrealized   = float(pnl.get("unrealized",     0))
        today_pnl    = float(pnl.get("today_realized", 0))
        today_trades = int(  pnl.get("today_trades",   0))
        open_pos     = int(  pnl.get("open_positions", 0))
        max_profit   = float(pnl.get("max_profit",     0))
        max_loss     = float(pnl.get("max_loss",       0))
        net_credit   = float(pnl.get("net_credit",     0))
        rr           = max_profit / max_loss if max_loss > 0 else 0

        reg_icon = {"BULL": "🟢", "BEAR": "🔴", "SIDE": "🟡"}.get(str(regime), "⚪")

        with cols[i]:
            st.markdown(f"#### {cfg.get('label', idx)}")
            c1, c2 = st.columns(2)
            c1.metric("Spot",   f"₹{float(spot):,.1f}" if spot else "--")
            c2.metric("Regime", f"{reg_icon} {regime}")
            c3, c4 = st.columns(2)
            c3.metric("Open Pos",     open_pos)
            c4.metric("Today Trades", today_trades)
            c5, c6 = st.columns(2)
            c5.metric("Unrealized",   _rs(unrealized, True), delta=_rs(unrealized, True))
            c6.metric("Today P&L",    _rs(today_pnl, True),  delta=_rs(today_pnl, True))
            if open_pos > 0:
                c7, c8 = st.columns(2)
                c7.metric("Max Profit", _rs(max_profit))
                c8.metric("Max Loss",   _rs(max_loss))
                c9, c10 = st.columns(2)
                c9.metric("Net Credit", _rs(net_credit))
                rr_str = f"1:{1/rr:.1f}" if rr > 0 else "--"
                rr_color = "🟢" if rr >= 0.33 else ("🟡" if rr >= 0.20 else "🔴")
                c10.metric("R:R", f"{rr_color} {rr_str}")

    st.divider()

    # ── Open Positions ────────────────────────────────────────────────────────
    st.subheader("📋 Open Positions")
    all_pos_raw = _parse(state.get("all_positions")) or {}
    any_open = False

    for idx, positions in all_pos_raw.items():
        if not isinstance(positions, list):
            continue
        open_positions = [p for p in positions if p.get("open")]
        if not open_positions:
            continue
        any_open = True

        for pi, pos in enumerate(open_positions):
            strategy   = pos.get("strategy", "--")
            entry_time = pos.get("entry_time", "--")
            unrealized = float(pos.get("unrealized", 0))
            max_profit = float(pos.get("max_profit", 0))
            max_loss   = float(pos.get("max_loss",   0))
            net_credit = float(pos.get("net_credit", 0))
            m          = pos.get("margin_info", {}) or {}
            cr         = float(m.get("credit_ratio", net_credit / (max_loss + net_credit) if (max_loss + net_credit) > 0 else 0))
            rr         = float(m.get("rr_ratio",     max_profit / max_loss if max_loss > 0 else 0))
            margin_req = float(m.get("margin_required", max_loss))
            peak_m     = float(m.get("peak_margin_est", 0))

            # Color coding
            rr_icon  = "🟢" if rr >= 0.33 else ("🟡" if rr >= 0.20 else "🔴")
            cr_icon  = "🟢" if cr >= 0.33 else ("🟡" if cr >= 0.20 else "🔴")
            pnl_icon = "🟢" if unrealized >= 0 else "🔴"

            with st.container():
                st.markdown(f"---")
                # Header row
                h1, h2, h3, h4 = st.columns([2, 2, 2, 1])
                h1.markdown(f"**{idx}** · `{strategy}`")
                h2.markdown(f"Entry: `{entry_time}`")
                h3.markdown(f"{pnl_icon} Unrealized: **{_rs(unrealized, True)}**")
                with h4:
                    if st.button(f"🔴 Close", key=f"close_pos_{idx}_{pi}",
                                 use_container_width=True):
                        _write_close_command(idx, "close_position", pi)
                        st.success(f"Close command sent for {strategy}")
                        time.sleep(0.5); st.rerun()

                # Risk metrics
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric("Max Profit",  _rs(max_profit),
                          help="Maximum profit if both legs expire worthless")
                m2.metric("Max Loss",    _rs(max_loss),
                          help="Maximum loss if spread fully breached")
                m3.metric("Net Credit",  _rs(net_credit),
                          help="Premium collected upfront")
                m4.metric("R:R",         f"{rr_icon} 1:{1/rr:.1f}" if rr > 0 else "--",
                          help="Risk:Reward. 🟢≥1:3  🟡1:5  🔴<1:5")
                m5.metric("Credit Ratio", f"{cr_icon} {cr:.1%}",
                          help="Credit as % of spread width. Higher = better trade quality")
                m6.metric("Margin Req",  _rs(margin_req),
                          help="Capital required to hold this position")

                # Explain R:R
                if rr < 0.20:
                    st.error(
                        f"⚠️ **Poor R:R ({rr_icon} 1:{1/rr:.0f})** — "
                        f"Credit (₹{net_credit:,.0f}) is only {cr:.1%} of spread width. "
                        f"This means you risk ₹{max_loss:,.0f} to make ₹{max_profit:,.0f}. "
                        f"Consider: raise TARGET_DELTA or lower SPREAD_WIDTH_POINTS in .env"
                    )
                elif rr < 0.33:
                    st.warning(
                        f"🟡 **Marginal R:R (1:{1/rr:.0f})** — "
                        f"Credit ratio {cr:.1%}. Acceptable but not ideal."
                    )

                # Legs table
                legs = pos.get("legs", [])
                if legs:
                    leg_data = []
                    for leg in legs:
                        entry = float(leg.get("price", 0))
                        ltp   = float(leg.get("ltp",   0))
                        lpnl  = float(leg.get("unrealized_pnl", 0))
                        chg   = ltp - entry if ltp > 0 else 0
                        leg_data.append({
                            "Side":    leg.get("side",   "--"),
                            "Type":    leg.get("type",   "--"),
                            "Strike":  f"₹{float(leg.get('strike',0)):,.0f}",
                            "Entry":   f"₹{entry:.2f}",
                            "LTP":     f"₹{ltp:.2f}" if ltp > 0 else "--",
                            "Change":  f"{'+'if chg>=0 else ''}{chg:.2f}" if ltp > 0 else "--",
                            "Qty":     leg.get("qty", 75),
                            "Leg P&L": f"₹{lpnl:+,.0f}",
                            "Symbol":  leg.get("symbol", "--"),
                        })
                    df_legs = pd.DataFrame(leg_data)

                    def _style_leg(row):
                        styles = [""] * len(row)
                        # Side column
                        si = df_legs.columns.get_loc("Side")
                        styles[si] = "color:red;font-weight:bold" if row["Side"] == "SELL" \
                                     else "color:green;font-weight:bold"
                        # Leg P&L column
                        pi2 = df_legs.columns.get_loc("Leg P&L")
                        try:
                            v = float(row["Leg P&L"].replace("₹","").replace(",","").replace("+",""))
                            styles[pi2] = "color:green;font-weight:bold" if v >= 0 \
                                          else "color:red;font-weight:bold"
                        except Exception:
                            pass
                        return styles

                    st.dataframe(
                        df_legs.style.apply(_style_leg, axis=1),
                        use_container_width=True, hide_index=True
                    )

    if not any_open:
        st.info("No open positions — bot is monitoring the market.")

    # ── Close All buttons ─────────────────────────────────────────────────────
    open_indices = [
        idx for idx, positions in all_pos_raw.items()
        if isinstance(positions, list) and any(p.get("open") for p in positions)
    ]
    if open_indices:
        st.divider()
        st.subheader("🔴 Close All")
        btn_cols = st.columns(len(open_indices) + (1 if len(open_indices) > 1 else 0))
        for i, idx in enumerate(open_indices):
            label  = INDEX_CONFIG.get(idx, {}).get("label", idx)
            pnl_v  = _parse(state.get(f"pnl_{idx}")) or {}
            unreal = float(pnl_v.get("unrealized", 0))
            if btn_cols[i].button(f"🔴 Close All {label}  ({_rs(unreal, True)})",
                                  type="primary", use_container_width=True,
                                  key=f"close_all_{idx}"):
                _write_close_command(idx, "close_all")
                st.success(f"Close all sent for {label}")
                time.sleep(0.5); st.rerun()
        if len(open_indices) > 1:
            if btn_cols[-1].button("🔴 Close ALL Indices", type="primary",
                                   use_container_width=True, key="close_all_all"):
                for idx in open_indices:
                    _write_close_command(idx, "close_all")
                st.success("Close all sent for all indices")
                time.sleep(0.5); st.rerun()

    # ── Today's closed trades ─────────────────────────────────────────────────
    st.divider()
    st.subheader("📅 Today's Closed Trades")
    try:
        from execution.paper_engine import load_trade_history
        history   = load_trade_history()
        today_str = date.today().isoformat()
        today_h   = [h for h in history if str(h.get("exit_time",""))[:10] == today_str]

        if today_h:
            t_pnl  = sum(float(h.get("pnl", 0)) for h in today_h)
            t_wins = [h for h in today_h if float(h.get("pnl", 0)) > 0]
            t_loss = [h for h in today_h if float(h.get("pnl", 0)) < 0]
            td1, td2, td3, td4 = st.columns(4)
            td1.metric("Trades Today",  len(today_h))
            td2.metric("Winners",       len(t_wins), delta=f"+{len(t_wins)}")
            td3.metric("Losers",        len(t_loss), delta=f"-{len(t_loss)}")
            td4.metric("Today Net P&L", _rs(t_pnl, True), delta=_rs(t_pnl, True))

            rows = []
            for h in today_h:
                pnl_v  = float(h.get("pnl", 0))
                mp     = float(h.get("max_profit", 0))
                ml     = float(h.get("max_loss",   0))
                nc     = float(h.get("net_credit", 0))
                rr     = mp / ml if ml > 0 else 0
                rows.append({
                    "Index":      h.get("index", "--"),
                    "Strategy":   h.get("strategy", "--"),
                    "Entry":      str(h.get("entry_time",""))[:16],
                    "Exit":       str(h.get("exit_time",""))[:16],
                    "Reason":     h.get("exit_reason","--"),
                    "Net Credit": f"₹{nc:,.0f}",
                    "Max Profit": f"₹{mp:,.0f}",
                    "Max Loss":   f"₹{ml:,.0f}",
                    "R:R":        f"1:{1/rr:.1f}" if rr > 0 else "--",
                    "P&L":        pnl_v,
                })
            df_today = pd.DataFrame(rows)

            def _style_pnl(v):
                try:
                    return "color:green;font-weight:bold" if float(v) >= 0 \
                           else "color:red;font-weight:bold"
                except Exception: return ""

            st.dataframe(
                df_today.style.map(_style_pnl, subset=["P&L"])
                              .format({"P&L": lambda x: f"₹{x:+,.0f}"}),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No trades closed today yet.")
    except Exception as e:
        st.warning(f"Could not load today's trades: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Backtest":
    st.title("📊 Strategy Backtest")
    st.info("Uses same XGBoost model, same strategy/delta/spread/SL/target from .env")

    with st.expander("⚙️ Backtest Settings", expanded=True):
        bc1, bc2, bc3 = st.columns(3)
        bt_index   = bc1.selectbox("Index", list(INDEX_CONFIG.keys()), index=0)
        bt_years   = bc2.slider("Years of data", 1, 5, 5)
        bt_capital = bc3.number_input("Initial Capital (₹)", value=500000, step=50000)
        lp1, lp2, lp3, lp4, lp5 = st.columns(5)
        lp1.metric("Stop Loss",       f"₹{SETTINGS.stop_loss:,.0f}")
        lp2.metric("Target",          f"₹{SETTINGS.target_profit:,.0f}")
        lp3.metric("Delta",           str(SETTINGS.target_delta))
        lp4.metric("Spread Width",    f"{SETTINGS.spread_width_points} pts")
        lp5.metric("Min Credit Ratio",f"{SETTINGS.min_credit_ratio:.0%}")
        bp1, bp2, bp3 = st.columns(3)
        bt_entry_bars = bp1.number_input("Entry every N bars", value=5,  min_value=1)
        bt_hold_bars  = bp2.number_input("Hold for N bars",    value=10, min_value=1)
        bt_commission = bp3.number_input("Commission/order",   value=20, min_value=0)

    run_bt = st.button("▶️ Run Backtest", type="primary", use_container_width=True)
    if run_bt:
        with st.spinner(f"Running {bt_years}-yr backtest on {bt_index}..."):
            try:
                from backtest.engine import run_backtest, BacktestConfig, default_backtest_config
                from dataclasses import asdict
                base_cfg = default_backtest_config(bt_index, years=bt_years)
                cfg = BacktestConfig(**{**asdict(base_cfg),
                    "initial_capital": float(bt_capital),
                    "entry_every_bars": int(bt_entry_bars),
                    "holding_bars": int(bt_hold_bars),
                    "commission_per_order": float(bt_commission)})
                try:
                    from broker.upstox import Broker
                    _broker = Broker()
                except Exception:
                    _broker = None
                result = run_backtest(cfg, broker=_broker)
                st.session_state["bt_result"] = result
                st.session_state["bt_label"]  = f"{bt_index} | {bt_years}yr | ₹{bt_capital:,}"
                st.success("✅ Backtest complete!")
            except Exception as e:
                st.error(f"Backtest failed: {e}")
                import traceback; st.code(traceback.format_exc())

    result = st.session_state.get("bt_result")
    if not result:
        st.info("Configure settings above and click ▶️ Run Backtest.")
        st.stop()

    st.subheader(f"Results: {st.session_state.get('bt_label','')}")
    m = result.metrics
    k1,k2,k3,k4 = st.columns(4)
    k1.metric("Total Trades", m.get("trades",0))
    k2.metric("Win Rate",     f"{m.get('win_rate_pct',0):.1f}%")
    k3.metric("Net P&L",      f"₹{m.get('net_pnl',0):+,.0f}")
    k4.metric("Return",       f"{m.get('return_pct',0):+.2f}%")
    k5,k6,k7,k8 = st.columns(4)
    k5.metric("Max Drawdown", f"₹{m.get('max_drawdown',0):,.0f}")
    k6.metric("Profit Factor",f"{m.get('profit_factor',0):.2f}" if m.get("profit_factor") else "--")
    k7.metric("Avg Trade",    f"₹{m.get('avg_trade',0):+,.0f}")
    k8.metric("Sharpe-like",  f"{m.get('sharpe_like',0):.3f}" if m.get("sharpe_like") else "--")

    equity_df = pd.DataFrame(result.equity_curve)
    if not equity_df.empty:
        st.subheader("Equity Curve")
        equity_df["timestamp"] = pd.to_datetime(equity_df["timestamp"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=equity_df["timestamp"], y=equity_df["equity"],
                                 mode="lines", name="Equity",
                                 line=dict(color="#00cc88", width=2)))
        fig.add_hline(y=result.config.get("initial_capital", 500000),
                      line_dash="dash", line_color="gray", annotation_text="Initial Capital")
        fig.update_layout(height=350, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig, use_container_width=True)

    trades_df = pd.DataFrame(result.trades)
    if not trades_df.empty:
        st.subheader("Trade Log")
        st.dataframe(trades_df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — HISTORY & ACCURACY
# ══════════════════════════════════════════════════════════════════════════════
else:
    st.title("📜 Trade History & Accuracy")
    try:
        from execution.paper_engine import load_trade_history
        history = load_trade_history()
    except Exception as e:
        st.error(f"Could not load trade history: {e}")
        history = []

    if not history:
        st.info("No closed trades yet.")
        st.stop()

    df   = pd.DataFrame(history)
    pnls = [float(h.get("pnl", 0)) for h in history]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p < 0]
    net  = sum(pnls)
    wr   = len(wins) / len(pnls) * 100 if pnls else 0
    pf   = round(sum(wins) / abs(sum(loss)), 2) if loss else None
    avg  = net / len(pnls) if pnls else 0

    st.subheader(f"All Closed Trades ({len(df)} total)")
    h1,h2,h3,h4,h5,h6 = st.columns(6)
    h1.metric("Total Trades",  len(df))
    h2.metric("Winners",       len(wins))
    h3.metric("Losers",        len(loss))
    h4.metric("Win Rate",      f"{wr:.1f}%")
    h5.metric("Net P&L",       _rs(net, True), delta=_rs(net, True))
    h6.metric("Profit Factor", f"{pf:.2f}" if pf else "--")

    h7,h8,h9 = st.columns(3)
    h7.metric("Avg Trade",   _rs(avg, True))
    h8.metric("Best Trade",  _rs(max(pnls), True) if pnls else "--")
    h9.metric("Worst Trade", _rs(min(pnls), True) if pnls else "--")

    # Today summary
    today_str = date.today().isoformat()
    today_h   = [h for h in history if str(h.get("exit_time",""))[:10] == today_str]
    if today_h:
        st.subheader("Today's Summary")
        t_pnl = sum(float(h.get("pnl",0)) for h in today_h)
        t_win = [h for h in today_h if float(h.get("pnl",0)) > 0]
        td1,td2,td3 = st.columns(3)
        td1.metric("Trades Today",  len(today_h))
        td2.metric("Winners Today", len(t_win))
        td3.metric("Today P&L",     _rs(t_pnl, True), delta=_rs(t_pnl, True))

    # Monthly P&L
    if "entry_time" in df.columns:
        df["entry_dt"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["month"]    = df["entry_dt"].dt.to_period("M").astype(str)
        monthly = df.groupby("month")["pnl"].sum().reset_index()
        monthly.columns = ["Month","Net P&L"]
        st.subheader("Monthly P&L")
        fig = px.bar(monthly, x="Month", y="Net P&L",
                     color="Net P&L", color_continuous_scale="RdYlGn",
                     color_continuous_midpoint=0)
        fig.update_layout(height=300, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig, use_container_width=True)

    # Strategy accuracy
    if "strategy" in df.columns:
        st.subheader("Strategy Accuracy")
        sb = df.groupby("strategy")["pnl"].agg(
            Trades="count", Wins=lambda x: (x>0).sum(),
            Total_PnL="sum", Avg_PnL="mean").reset_index()
        sb["Win Rate"]  = (sb["Wins"]/sb["Trades"]*100).round(1).astype(str)+"%"
        sb["Total_PnL"] = sb["Total_PnL"].map(lambda x: f"₹{x:+,.0f}")
        sb["Avg_PnL"]   = sb["Avg_PnL"].map(lambda x: f"₹{x:+,.0f}")
        st.dataframe(sb, use_container_width=True, hide_index=True)

    # Full log
    st.subheader("Full Trade Log")
    cols_show = [c for c in ["index","strategy","entry_time","exit_time",
                              "exit_reason","net_credit","max_profit","max_loss","pnl"]
                 if c in df.columns]
    disp = df[cols_show].copy()
    for col in ["net_credit","max_profit","max_loss"]:
        if col in disp.columns:
            disp[col] = disp[col].map(lambda x: f"₹{float(x):,.0f}")
    if "pnl" in disp.columns:
        disp["pnl"] = disp["pnl"].map(lambda x: f"₹{float(x):+,.0f}")
    st.dataframe(disp, use_container_width=True, hide_index=True)
