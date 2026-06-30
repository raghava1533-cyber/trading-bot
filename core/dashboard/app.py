"""
dashboard/app.py  ─  Algo Trading Bot  ─  Full Live Dashboard
Layout: STACKED (one index per row, not side-by-side)
Pages : Live Trading | History & Accuracy | Backtest | Settings
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

if st.session_state.get("_page","Live Trading") == "Live Trading":
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=5000, key="ar")
    except ImportError:
        pass

st.markdown("""
<style>
[data-testid="metric-container"]{background:#1e2535;border:1px solid #2d3748;border-radius:8px;padding:10px 14px;}
.section-title{font-size:1.05rem;font-weight:700;color:#e2e8f0;border-left:4px solid #4299e1;padding-left:10px;margin:16px 0 8px 0;}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.badge-bull{background:#1c4532;color:#68d391;border:1px solid #276749;}
.badge-bear{background:#742a2a;color:#fc8181;border:1px solid #9b2c2c;}
.badge-side{background:#744210;color:#f6e05e;border:1px solid #975a16;}
.badge-live{background:#1a365d;color:#63b3ed;border:1px solid #2b6cb0;}
.green{color:#48bb78;font-weight:700;} .red{color:#fc8181;font-weight:700;}
.muted{color:#718096;font-size:0.82rem;}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _read_state() -> dict:
    try:
        import redis as _r
        rc = _r.Redis.from_url(SETTINGS.redis_url, decode_responses=True, socket_connect_timeout=1)
        rc.ping()
        keys = rc.keys("*")
        if keys:
            return {k: v for k, v in zip(keys, rc.mget(keys)) if v}
    except Exception:
        pass
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE,"r",encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _parse(raw):
    if raw is None: return None
    if isinstance(raw,(dict,list)): return raw
    try: return json.loads(str(raw))
    except Exception:
        try: return ast.literal_eval(str(raw))
        except Exception: return None

def rs(v, sign=False):
    try:
        f = float(v)
        s = "+" if sign and f > 0 else ""
        return f"Rs{s}{f:,.0f}"
    except Exception: return "--"

def rr_label(rr_ratio):
    if rr_ratio <= 0: return "--", "🔴"
    inv = 1/rr_ratio
    icon = "🟢" if rr_ratio >= 0.33 else ("🟡" if rr_ratio >= 0.20 else "🔴")
    return f"1:{inv:.1f}", icon

def regime_badge(r):
    cls = {"BULL":"badge-bull","BEAR":"badge-bear","SIDE":"badge-side"}.get(r,"badge-live")
    icon = {"BULL":"▲","BEAR":"▼","SIDE":"◆"}.get(r,"?")
    return f'<span class="badge {cls}">{icon} {r or "--"}</span>'

def pnl_html(v):
    try:
        f = float(v)
        cls = "green" if f >= 0 else "red"
        s = "+" if f > 0 else ""
        return f'<span class="{cls}">Rs{s}{f:,.0f}</span>'
    except Exception: return "--"

def _write_close_cmd(index: str, action: str = "close_all", pos_idx: int = -1):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE,"r",encoding="utf-8") as f:
                data = json.load(f)
        cmds = data.get("close_commands",[])
        if isinstance(cmds,str):
            try: cmds = json.loads(cmds)
            except Exception: cmds = []
        cmds.append({"index":index,"action":action,"position_index":pos_idx})
        data["close_commands"] = cmds
        with open(STATE_FILE,"w",encoding="utf-8") as f:
            json.dump(data,f)
        st.toast(f"Close command sent for {index}")
    except Exception as e:
        st.error(f"Failed: {e}")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 Algo Bot")
    page = st.radio("Navigate",
        ["Live Trading","History & Accuracy","Backtest","Settings"], key="_page")
    st.divider()
    bot_ok = os.path.exists(STATE_FILE) and (time.time()-os.path.getmtime(STATE_FILE))<120
    if bot_ok:
        st.markdown('<span class="badge badge-live">● BOT RUNNING</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="badge badge-bear">● BOT STOPPED</span>', unsafe_allow_html=True)
    st.divider()
    st.caption("Active Settings")
    st.write(f"**Indices:** {', '.join(SETTINGS.active_indices)}")
    st.write(f"**Stop Loss:** {rs(SETTINGS.stop_loss)}")
    st.write(f"**Target:** {rs(SETTINGS.target_profit)}")
    st.write(f"**Delta:** {SETTINGS.target_delta}")
    st.write(f"**Spread:** {SETTINGS.spread_width_points} pts")
    st.write(f"**Min Credit:** {SETTINGS.min_credit_ratio:.0%}")
    st.write(f"**Mode:** {'DRY RUN' if SETTINGS.dry_run else 'LIVE'}")
    st.divider()
    st.caption("R:R Guide")
    st.write("🟢 >= 1:3  good  (credit >= 33%)")
    st.write("🟡  1:5   ok    (credit >= 20%)")
    st.write("🔴 < 1:5  poor  (credit < 20%)")
    st.divider()
    if st.button("Refresh", use_container_width=True):
        st.rerun()

# =============================================================================
# PAGE 1 — LIVE TRADING
# =============================================================================
if page == "Live Trading":
    hc1, hc2 = st.columns([5,1])
    with hc1:
        st.title("Live Trading Dashboard")
    with hc2:
        age = (time.time()-os.path.getmtime(STATE_FILE)) if os.path.exists(STATE_FILE) else 9999
        if age < 30:   st.success(f"Live ({age:.0f}s)")
        elif age < 120: st.warning(f"{age:.0f}s ago")
        else:           st.error("Bot stopped")

    if not os.path.exists(STATE_FILE):
        st.error("Bot not running.  Start:  python core/main_async.py")
        st.stop()

    state = _read_state()
    if not state:
        st.warning("Waiting for first bot cycle...")
        st.stop()

    active_indices = list(SETTINGS.active_indices)
    all_pos_raw    = _parse(state.get("all_positions")) or {}

    # Overall P&L
    total_unreal = total_today = total_real = 0.0
    total_open = total_trades = 0
    for idx in active_indices:
        p = _parse(state.get(f"pnl_{idx}")) or {}
        total_unreal  += float(p.get("unrealized",0))
        total_today   += float(p.get("today_realized",0))
        total_real    += float(p.get("realized",0))
        total_open    += int(p.get("open_positions",0))
        total_trades  += int(p.get("today_trades",0))

    st.markdown('<div class="section-title">Overall P&L</div>', unsafe_allow_html=True)
    oc1,oc2,oc3,oc4,oc5,oc6 = st.columns(6)
    oc1.metric("Unrealized",     rs(total_unreal,True),  delta=rs(total_unreal,True))
    oc2.metric("Today Realized", rs(total_today,True),   delta=rs(total_today,True))
    oc3.metric("Session Real.",  rs(total_real,True))
    oc4.metric("Session Total",  rs(total_real+total_unreal,True))
    oc5.metric("Open Positions", total_open)
    oc6.metric("Today Trades",   total_trades)
    st.divider()

    # Per-index rows — STACKED (one per row, full width)
    st.markdown('<div class="section-title">Index Status</div>', unsafe_allow_html=True)
    for idx in active_indices:
        cfg    = INDEX_CONFIG.get(idx,{})
        lbl    = cfg.get("label",idx)
        spot   = state.get(f"spot_{idx}")
        regime = state.get(f"regime_{idx}","--")
        pnl    = _parse(state.get(f"pnl_{idx}")) or {}
        unreal       = float(pnl.get("unrealized",0))
        today_pnl    = float(pnl.get("today_realized",0))
        today_trades = int(pnl.get("today_trades",0))
        open_pos     = int(pnl.get("open_positions",0))
        max_profit   = float(pnl.get("max_profit",0))
        max_loss     = float(pnl.get("max_loss",0))
        net_credit   = float(pnl.get("net_credit",0))
        rr_ratio     = max_profit/max_loss if max_loss>0 else 0
        rr_str, rr_icon = rr_label(rr_ratio)

        # Index header
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
            f'<span style="font-size:1.2rem;font-weight:800;color:#e2e8f0;">{lbl}</span>'
            f'{regime_badge(regime)}'
            f'<span class="muted" style="margin-left:auto;">Lot: {cfg.get("lot_size","--")}</span>'
            f'</div>',
            unsafe_allow_html=True
        )
        # Row 1: spot + P&L metrics (7 columns, full width)
        r1 = st.columns(7)
        r1[0].metric("Spot",          f"Rs{float(spot):,.1f}" if spot else "--")
        r1[1].metric("Open Pos",      open_pos)
        r1[2].metric("Today Trades",  today_trades)
        r1[3].metric("Unrealized",    rs(unreal,True),    delta=rs(unreal,True))
        r1[4].metric("Today P&L",     rs(today_pnl,True), delta=rs(today_pnl,True))
        r1[5].metric("Session Real.", rs(float(pnl.get("realized",0)),True))
        r1[6].metric("Session Total", rs(float(pnl.get("total",0)),True))

        # Row 2: risk metrics (only when position open)
        if open_pos > 0:
            r2 = st.columns(5)
            r2[0].metric("Max Profit",   rs(max_profit),
                         help="Max profit if all legs expire worthless")
            r2[1].metric("Max Loss",     rs(max_loss),
                         help="Max loss if spread fully breached")
            r2[2].metric("Net Credit",   rs(net_credit),
                         help="Premium collected upfront")
            r2[3].metric("R : R",        f"{rr_icon} {rr_str}",
                         help="Risk:Reward. Green>=1:3  Yellow=1:5  Red<1:5")
            r2[4].metric("Credit Ratio", f"{rr_ratio*100:.1f}%",
                         help="Credit as % of spread width. Higher = better")
            if rr_ratio > 0 and rr_ratio < 0.20:
                st.error(
                    f"Poor R:R ({rr_icon} {rr_str}) — "
                    f"Credit Rs{net_credit:,.0f} is only {rr_ratio*100:.1f}% of spread. "
                    f"Risking Rs{max_loss:,.0f} to make Rs{max_profit:,.0f}. "
                    f"Fix: raise TARGET_DELTA or lower SPREAD_WIDTH_POINTS in .env"
                )
            elif rr_ratio > 0 and rr_ratio < 0.33:
                st.warning(f"Marginal R:R ({rr_str}) — credit ratio {rr_ratio*100:.1f}%")
        st.markdown("---")

    # Open Positions — STACKED
    st.markdown('<div class="section-title">Open Positions</div>', unsafe_allow_html=True)
    any_open = False
    for idx, positions in all_pos_raw.items():
        if not isinstance(positions,list): continue
        open_list = [p for p in positions if p.get("open")]
        if not open_list: continue
        any_open = True
        for pi, pos in enumerate(open_list):
            strategy   = pos.get("strategy","--")
            entry_time = pos.get("entry_time","--")
            unreal     = float(pos.get("unrealized",0))
            max_profit = float(pos.get("max_profit",0))
            max_loss   = float(pos.get("max_loss",0))
            net_credit = float(pos.get("net_credit",0))
            mi         = pos.get("margin_info",{}) or {}
            cr         = float(mi.get("credit_ratio",
                               net_credit/(max_loss+net_credit) if (max_loss+net_credit)>0 else 0))
            rr         = float(mi.get("rr_ratio",
                               max_profit/max_loss if max_loss>0 else 0))
            margin_req = float(mi.get("margin_required",max_loss))
            rr_str2, rr_icon2 = rr_label(rr)

            # Position header row
            ph1,ph2,ph3,ph4,ph5 = st.columns([2,2,2,2,1])
            ph1.markdown(f"**{idx}** · `{strategy}`")
            ph2.markdown(f'<span class="muted">Entry: {entry_time}</span>',
                         unsafe_allow_html=True)
            ph3.markdown(pnl_html(unreal)+" unrealized", unsafe_allow_html=True)
            ph4.markdown(f"R:R {rr_icon2} **{rr_str2}**")
            with ph5:
                if st.button("Close", key=f"cp_{idx}_{pi}",
                             use_container_width=True, type="primary"):
                    _write_close_cmd(idx,"close_position",pi)
                    time.sleep(0.4); st.rerun()

            # Risk metrics row
            rm = st.columns(6)
            rm[0].metric("Max Profit",   rs(max_profit))
            rm[1].metric("Max Loss",     rs(max_loss))
            rm[2].metric("Net Credit",   rs(net_credit))
            rm[3].metric("R : R",        f"{rr_icon2} {rr_str2}")
            rm[4].metric("Credit Ratio", f"{cr*100:.1f}%")
            rm[5].metric("Margin Req",   rs(margin_req))

            # Legs table
            legs = pos.get("legs",[])
            if legs:
                rows = []
                for leg in legs:
                    entry = float(leg.get("price",0))
                    ltp   = float(leg.get("ltp",0))
                    lpnl  = float(leg.get("unrealized_pnl",0))
                    chg   = ltp-entry if ltp>0 else 0
                    rows.append({
                        "Side":   leg.get("side","--"),
                        "Type":   leg.get("type","--"),
                        "Strike": f"Rs{float(leg.get('strike',0)):,.0f}",
                        "Entry":  f"Rs{entry:.2f}",
                        "LTP":    f"Rs{ltp:.2f}" if ltp>0 else "--",
                        "Change": f"{'+'if chg>=0 else ''}{chg:.2f}" if ltp>0 else "--",
                        "Qty":    leg.get("qty",75),
                        "Leg PnL":lpnl,
                        "Symbol": leg.get("symbol","--"),
                    })
                df_l = pd.DataFrame(rows)
                def _sl(row):
                    out = [""]*len(row)
                    si = df_l.columns.get_loc("Side")
                    out[si] = ("color:#fc8181;font-weight:700" if row["Side"]=="SELL"
                               else "color:#68d391;font-weight:700")
                    pi3 = df_l.columns.get_loc("Leg PnL")
                    try:
                        v = float(row["Leg PnL"])
                        out[pi3] = ("color:#68d391;font-weight:700" if v>=0
                                    else "color:#fc8181;font-weight:700")
                    except Exception: pass
                    return out
                st.dataframe(
                    df_l.style.apply(_sl,axis=1).format({"Leg PnL":lambda x:f"Rs{x:+,.0f}"}),
                    use_container_width=True, hide_index=True)
            st.markdown("---")

    if not any_open:
        st.info("No open positions — bot is monitoring the market.")

    # Close All buttons
    open_indices = [
        idx for idx,positions in all_pos_raw.items()
        if isinstance(positions,list) and any(p.get("open") for p in positions)
    ]
    if open_indices:
        st.markdown('<div class="section-title">Manual Close</div>', unsafe_allow_html=True)
        btn_cols = st.columns(min(len(open_indices)+1,4))
        for i,idx in enumerate(open_indices):
            lbl2   = INDEX_CONFIG.get(idx,{}).get("label",idx)
            pnl_v  = _parse(state.get(f"pnl_{idx}")) or {}
            unreal = float(pnl_v.get("unrealized",0))
            if btn_cols[i%4].button(f"Close All {lbl2}  ({rs(unreal,True)})",
                                    type="primary", use_container_width=True,
                                    key=f"ca_{idx}"):
                _write_close_cmd(idx,"close_all")
                time.sleep(0.4); st.rerun()
        if len(open_indices)>1:
            if btn_cols[-1].button("Close ALL Indices", type="primary",
                                   use_container_width=True, key="ca_all"):
                for idx in open_indices:
                    _write_close_cmd(idx,"close_all")
                time.sleep(0.4); st.rerun()

    # Today's closed trades
    st.divider()
    st.markdown('<div class="section-title">Today\'s Closed Trades</div>',
                unsafe_allow_html=True)
    try:
        from execution.paper_engine import load_trade_history
        history   = load_trade_history()
        today_str = date.today().isoformat()
        today_h   = [h for h in history if str(h.get("exit_time",""))[:10]==today_str]
        if today_h:
            t_pnl  = sum(float(h.get("pnl",0)) for h in today_h)
            t_wins = [h for h in today_h if float(h.get("pnl",0))>0]
            t_loss = [h for h in today_h if float(h.get("pnl",0))<0]
            td1,td2,td3,td4 = st.columns(4)
            td1.metric("Trades Today",  len(today_h))
            td2.metric("Winners",       len(t_wins))
            td3.metric("Losers",        len(t_loss))
            td4.metric("Today Net P&L", rs(t_pnl,True), delta=rs(t_pnl,True))
            rows = []
            for h in today_h:
                pv=float(h.get("pnl",0)); mp=float(h.get("max_profit",0))
                ml=float(h.get("max_loss",0)); nc=float(h.get("net_credit",0))
                rr=mp/ml if ml>0 else 0; rr_s,rr_i=rr_label(rr)
                rows.append({"Index":h.get("index","--"),"Strategy":h.get("strategy","--"),
                    "Entry":str(h.get("entry_time",""))[:16],"Exit":str(h.get("exit_time",""))[:16],
                    "Reason":h.get("exit_reason","--"),"Net Credit":rs(nc),
                    "Max Profit":rs(mp),"Max Loss":rs(ml),"R:R":f"{rr_i} {rr_s}","PnL":pv})
            df_t = pd.DataFrame(rows)
            st.dataframe(
                df_t.style.map(
                    lambda v: ("color:#68d391;font-weight:700" if isinstance(v,float) and v>=0
                               else ("color:#fc8181;font-weight:700" if isinstance(v,float) else "")),
                    subset=["PnL"]
                ).format({"PnL":lambda x:f"Rs{x:+,.0f}"}),
                use_container_width=True, hide_index=True)
        else:
            st.info("No trades closed today yet.")
    except Exception as e:
        st.warning(f"Could not load today's trades: {e}")

# =============================================================================
# PAGE 2 — HISTORY & ACCURACY
# =============================================================================
elif page == "History & Accuracy":
    st.title("Trade History & Accuracy")
    try:
        from execution.paper_engine import load_trade_history
        history = load_trade_history()
    except Exception as e:
        st.error(f"Could not load trade history: {e}"); history = []

    if not history:
        st.info("No closed trades yet. Run the bot to generate history.")
        st.stop()

    df   = pd.DataFrame(history)
    pnls = [float(h.get("pnl",0)) for h in history]
    wins = [p for p in pnls if p>0]
    loss = [p for p in pnls if p<0]
    net  = sum(pnls)
    wr   = len(wins)/len(pnls)*100 if pnls else 0
    pf   = round(sum(wins)/abs(sum(loss)),2) if loss else None
    avg  = net/len(pnls) if pnls else 0

    st.markdown('<div class="section-title">All-Time Statistics</div>', unsafe_allow_html=True)
    s1,s2,s3,s4,s5,s6 = st.columns(6)
    s1.metric("Total Trades",  len(df))
    s2.metric("Winners",       len(wins))
    s3.metric("Losers",        len(loss))
    s4.metric("Win Rate",      f"{wr:.1f}%")
    s5.metric("Net P&L",       rs(net,True), delta=rs(net,True))
    s6.metric("Profit Factor", f"{pf:.2f}" if pf else "--")
    s7,s8,s9,s10 = st.columns(4)
    s7.metric("Avg Trade",    rs(avg,True))
    s8.metric("Best Trade",   rs(max(pnls),True) if pnls else "--")
    s9.metric("Worst Trade",  rs(min(pnls),True) if pnls else "--")
    s10.metric("Gross Profit",rs(sum(wins)) if wins else "--")

    today_str = date.today().isoformat()
    today_h   = [h for h in history if str(h.get("exit_time",""))[:10]==today_str]
    if today_h:
        st.markdown('<div class="section-title">Today\'s Summary</div>', unsafe_allow_html=True)
        t_pnl = sum(float(h.get("pnl",0)) for h in today_h)
        t_win = [h for h in today_h if float(h.get("pnl",0))>0]
        td1,td2,td3,td4 = st.columns(4)
        td1.metric("Trades Today",  len(today_h))
        td2.metric("Winners Today", len(t_win))
        td3.metric("Losers Today",  len(today_h)-len(t_win))
        td4.metric("Today P&L",     rs(t_pnl,True), delta=rs(t_pnl,True))

    if "entry_time" in df.columns:
        df["entry_dt"] = pd.to_datetime(df["entry_time"],errors="coerce")
        df["month"]    = df["entry_dt"].dt.to_period("M").astype(str)
        monthly = df.groupby("month")["pnl"].sum().reset_index()
        monthly.columns = ["Month","Net PnL"]
        st.markdown('<div class="section-title">Monthly P&L</div>', unsafe_allow_html=True)
        fig = px.bar(monthly, x="Month", y="Net PnL",
                     color="Net PnL",
                     color_continuous_scale=[[0,"#fc8181"],[0.5,"#f6e05e"],[1,"#68d391"]],
                     color_continuous_midpoint=0)
        fig.update_layout(height=260, margin=dict(l=0,r=0,t=20,b=0),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font_color="#e2e8f0")
        st.plotly_chart(fig, use_container_width=True)

    if "exit_time" in df.columns:
        df_eq = df.copy()
        df_eq["exit_dt"] = pd.to_datetime(df_eq["exit_time"],errors="coerce")
        df_eq = df_eq.sort_values("exit_dt")
        df_eq["cumulative"] = df_eq["pnl"].cumsum()
        st.markdown('<div class="section-title">Equity Curve</div>', unsafe_allow_html=True)
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=df_eq["exit_dt"], y=df_eq["cumulative"],
            mode="lines+markers", name="Cumulative P&L",
            line=dict(color="#63b3ed",width=2),
            fill="tozeroy", fillcolor="rgba(99,179,237,0.1)"))
        fig2.add_hline(y=0, line_dash="dash", line_color="#718096")
        fig2.update_layout(height=240, margin=dict(l=0,r=0,t=20,b=0),
                           paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           font_color="#e2e8f0")
        st.plotly_chart(fig2, use_container_width=True)

    if "strategy" in df.columns:
        st.markdown('<div class="section-title">Strategy Accuracy</div>', unsafe_allow_html=True)
        sb = df.groupby("strategy")["pnl"].agg(
            Trades="count", Wins=lambda x:(x>0).sum(),
            Total_PnL="sum", Avg_PnL="mean", Best="max", Worst="min").reset_index()
        sb["Win Rate"]  = (sb["Wins"]/sb["Trades"]*100).round(1).astype(str)+"%"
        sb["Total_PnL"] = sb["Total_PnL"].map(lambda x:f"Rs{x:+,.0f}")
        sb["Avg_PnL"]   = sb["Avg_PnL"].map(lambda x:f"Rs{x:+,.0f}")
        sb["Best"]      = sb["Best"].map(lambda x:f"Rs{x:+,.0f}")
        sb["Worst"]     = sb["Worst"].map(lambda x:f"Rs{x:+,.0f}")
        st.dataframe(sb, use_container_width=True, hide_index=True)

    if "exit_reason" in df.columns:
        st.markdown('<div class="section-title">Exit Reason Breakdown</div>', unsafe_allow_html=True)
        er = df.groupby("exit_reason")["pnl"].agg(Count="count",Total="sum",Avg="mean").reset_index()
        er["Total"] = er["Total"].map(lambda x:f"Rs{x:+,.0f}")
        er["Avg"]   = er["Avg"].map(lambda x:f"Rs{x:+,.0f}")
        st.dataframe(er, use_container_width=True, hide_index=True)

    st.markdown('<div class="section-title">Full Trade Log (newest first)</div>', unsafe_allow_html=True)
    cols_show = [c for c in ["index","strategy","entry_time","exit_time",
                              "exit_reason","net_credit","max_profit","max_loss","pnl"]
                 if c in df.columns]
    disp = df[cols_show].copy().sort_values("exit_time",ascending=False)
    for col in ["net_credit","max_profit","max_loss"]:
        if col in disp.columns:
            disp[col] = disp[col].map(lambda x:f"Rs{float(x):,.0f}")
    if "pnl" in disp.columns:
        disp["pnl"] = disp["pnl"].map(lambda x:f"Rs{float(x):+,.0f}")
    st.dataframe(disp, use_container_width=True, hide_index=True)

# =============================================================================
# PAGE 3 — BACKTEST
# =============================================================================
elif page == "Backtest":
    st.title("Strategy Backtest")
    st.info("Uses same XGBoost model + same strategy/delta/spread/SL/target from .env")
    with st.expander("Backtest Settings", expanded=True):
        bc1,bc2,bc3 = st.columns(3)
        bt_index   = bc1.selectbox("Index", list(INDEX_CONFIG.keys()), index=0)
        bt_years   = bc2.slider("Years of data", 1, 5, 5)
        bt_capital = bc3.number_input("Initial Capital (Rs)", value=500000, step=50000)
        lp1,lp2,lp3,lp4,lp5 = st.columns(5)
        lp1.metric("Stop Loss",        rs(SETTINGS.stop_loss))
        lp2.metric("Target",           rs(SETTINGS.target_profit))
        lp3.metric("Delta",            str(SETTINGS.target_delta))
        lp4.metric("Spread Width",     f"{SETTINGS.spread_width_points} pts")
        lp5.metric("Min Credit Ratio", f"{SETTINGS.min_credit_ratio:.0%}")
        bp1,bp2,bp3 = st.columns(3)
        bt_entry = bp1.number_input("Entry every N bars", value=5,  min_value=1)
        bt_hold  = bp2.number_input("Hold for N bars",    value=10, min_value=1)
        bt_comm  = bp3.number_input("Commission/order",   value=20, min_value=0)
    if st.button("Run Backtest", type="primary", use_container_width=True):
        with st.spinner(f"Running {bt_years}-yr backtest on {bt_index}..."):
            try:
                from backtest.engine import run_backtest, BacktestConfig, default_backtest_config
                from dataclasses import asdict
                base = default_backtest_config(bt_index, years=bt_years)
                cfg  = BacktestConfig(**{**asdict(base),
                    "initial_capital":float(bt_capital),"entry_every_bars":int(bt_entry),
                    "holding_bars":int(bt_hold),"commission_per_order":float(bt_comm)})
                try:
                    from broker.upstox import Broker; _b = Broker()
                except Exception: _b = None
                result = run_backtest(cfg, broker=_b)
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
    k1,k2,k3,k4 = st.columns(4)
    k1.metric("Total Trades",  m.get("trades",0))
    k2.metric("Win Rate",      f"{m.get('win_rate_pct',0):.1f}%")
    k3.metric("Net P&L",       f"Rs{m.get('net_pnl',0):+,.0f}")
    k4.metric("Return",        f"{m.get('return_pct',0):+.2f}%")
    k5,k6,k7,k8 = st.columns(4)
    k5.metric("Max Drawdown",  f"Rs{m.get('max_drawdown',0):,.0f}")
    k6.metric("Profit Factor", f"{m.get('profit_factor',0):.2f}" if m.get("profit_factor") else "--")
    k7.metric("Avg Trade",     f"Rs{m.get('avg_trade',0):+,.0f}")
    k8.metric("Sharpe-like",   f"{m.get('sharpe_like',0):.3f}" if m.get("sharpe_like") else "--")
    eq = pd.DataFrame(result.equity_curve)
    if not eq.empty:
        eq["timestamp"] = pd.to_datetime(eq["timestamp"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq["timestamp"],y=eq["equity"],mode="lines",
            line=dict(color="#68d391",width=2),fill="tozeroy",fillcolor="rgba(104,211,145,0.1)"))
        fig.add_hline(y=result.config.get("initial_capital",500000),
                      line_dash="dash",line_color="#718096",annotation_text="Initial Capital")
        fig.update_layout(height=300,margin=dict(l=0,r=0,t=20,b=0),
                          paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",
                          font_color="#e2e8f0")
        st.plotly_chart(fig, use_container_width=True)
    td = pd.DataFrame(result.trades)
    if not td.empty:
        st.subheader("Trade Log")
        st.dataframe(td, use_container_width=True, hide_index=True)

# =============================================================================
# PAGE 4 — SETTINGS
# =============================================================================
else:
    st.title("Settings & Configuration")
    st.markdown('<div class="section-title">Current Settings</div>', unsafe_allow_html=True)
    cfg_data = {
        "Setting":["ACTIVE_INDICES","POLL_INTERVAL_SECONDS","TRADE_COOLDOWN_SECONDS",
                   "MAX_TRADES_PER_DAY","STOP_LOSS","TARGET_PROFIT","TARGET_DELTA",
                   "MIN_CREDIT_RATIO","SPREAD_WIDTH_POINTS","DRY_RUN",
                   "MARKET_OPEN_TIME","MARKET_CLOSE_TIME"],
        "Value":[", ".join(SETTINGS.active_indices),str(SETTINGS.poll_interval_seconds),
                 str(SETTINGS.trade_cooldown_seconds),str(SETTINGS.max_trades_per_day),
                 rs(SETTINGS.stop_loss),rs(SETTINGS.target_profit),str(SETTINGS.target_delta),
                 f"{SETTINGS.min_credit_ratio:.0%}",f"{SETTINGS.spread_width_points} pts",
                 "DRY RUN" if SETTINGS.dry_run else "LIVE",
                 SETTINGS.market_open_time.strftime("%H:%M"),
                 SETTINGS.market_close_time.strftime("%H:%M")],
        "Description":["Indices to trade","Poll interval (seconds)","Min gap between trades",
                        "Max trades per day","Auto-close on loss","Auto-close on profit",
                        "Target delta for strike selection","Min credit/spread ratio (trade quality)",
                        "Spread width in index points","Paper trading mode",
                        "Market open IST","Market close IST"],
    }
    st.dataframe(pd.DataFrame(cfg_data), use_container_width=True, hide_index=True)

    st.markdown('<div class="section-title">R:R Explained</div>', unsafe_allow_html=True)
    st.markdown("""
**Why Max Loss >> Max Profit?**

For a BULL_PUT spread (sell higher PE, buy lower PE):
- **Net Credit** = (sell_premium - buy_premium) x lot_size = your max profit
- **Max Loss** = (spread_width_pts - credit_per_share) x lot_size

| Scenario | Sell | Buy | Credit/share | Max Profit | Max Loss | R:R |
|---|---|---|---|---|---|---|
| Poor (blocked) | Rs12 | Rs1.75 | Rs10.25 (5%) | Rs769 | Rs14,231 | 1:18 RED |
| Good (allowed) | Rs60 | Rs10 | Rs50 (25%) | Rs3,750 | Rs11,250 | 1:3 GREEN |

**Fix already applied:** MIN_CREDIT_RATIO=0.25 blocks poor trades.
    """)

    st.markdown('<div class="section-title">Index Config</div>', unsafe_allow_html=True)
    idx_rows = [{"Index":k,"Label":v.get("label",""),"Lot Size":v.get("lot_size",""),
                 "Range":v.get("range_size",""),"YF Ticker":v.get("yf_ticker","")}
                for k,v in INDEX_CONFIG.items()]
    st.dataframe(pd.DataFrame(idx_rows), use_container_width=True, hide_index=True)
