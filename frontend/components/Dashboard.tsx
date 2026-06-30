use client";
/**
 * Dashboard.tsx - Full trading bot dashboard
 * Connects via WebSocket (live) with REST polling fallback.
 * Shows: positions, legs, max profit/loss, today P&L, history, close buttons.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";

// ── Types ─────────────────────────────────────────────────────────────────────
interface Leg {
  side: string; type: string; strike: number;
  price: number; ltp: number; unrealized_pnl: number;
  qty: number; symbol: string;
}
interface Position {
  strategy: string; index: string; entry_time: string;
  open: boolean; unrealized: number;
  max_profit: number; max_loss: number; net_credit: number;
  margin_info: Record<string, number>;
  legs: Leg[];
}
interface PnL {
  realized: number; unrealized: number; total: number;
  open_positions: number; today_realized: number; today_trades: number;
  max_profit: number; max_loss: number; net_credit: number;
}
interface IndexState { pnl: PnL | null; spot: number | null; regime: string | null; }
interface Trade {
  strategy: string; index: string; entry_time: string; exit_time: string;
  exit_reason: string; pnl: number; max_profit: number; max_loss: number; net_credit: number;
}
interface BotState {
  indices: Record<string, IndexState>;
  all_positions: Record<string, Position[]>;
  trade_history: Trade[];
  last_update: string;
}

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const WS_URL   = API_BASE.replace(/^http/, "ws") + "/ws";
const INDICES  = ["NIFTY", "BANKNIFTY", "SENSEX"];

// ── Helpers ───────────────────────────────────────────────────────────────────
const rs = (v: number | null | undefined, showSign = false) => {
  if (v == null) return "—";
  const s = showSign && v > 0 ? "+" : "";
  return `₹${s}${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
};
const pct = (v: number | null | undefined) => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const clr = (v: number | null | undefined) =>
  v == null ? "text-gray-400" : v >= 0 ? "text-emerald-400" : "text-red-400";
const regimeBadge = (r: string | null) => {
  if (r === "BULL") return "bg-emerald-900 text-emerald-300 border-emerald-700";
  if (r === "BEAR") return "bg-red-900 text-red-300 border-red-700";
  if (r === "SIDE") return "bg-yellow-900 text-yellow-300 border-yellow-700";
  return "bg-gray-800 text-gray-400 border-gray-600";
};
const regimeIcon = (r: string | null) =>
  r === "BULL" ? "▲" : r === "BEAR" ? "▼" : r === "SIDE" ? "◆" : "?";

// ── Metric card ───────────────────────────────────────────────────────────────
function Metric({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-xl font-bold ${color ?? "text-white"}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [state, setState]       = useState<BotState | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const [tab, setTab]           = useState<"live" | "history">("live");
  const [closing, setClosing]   = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);

  const applyState = useCallback((data: BotState) => {
    setState(data);
    setLastUpdate(data.last_update ?? new Date().toISOString());
  }, []);

  // WebSocket
  useEffect(() => {
    let retryTimer: ReturnType<typeof setTimeout>;
    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen  = () => setConnected(true);
      ws.onmessage = (e) => { try { applyState(JSON.parse(e.data)); } catch {} };
      ws.onclose = () => {
        setConnected(false);
        retryTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }
    connect();
    return () => { wsRef.current?.close(); clearTimeout(retryTimer); };
  }, [applyState]);

  // REST fallback
  useEffect(() => {
    if (connected) return;
    const id = setInterval(async () => {
      try {
        const r = await fetch(`${API_BASE}/state`);
        applyState(await r.json());
      } catch {}
    }, 5000);
    return () => clearInterval(id);
  }, [connected, applyState]);

  // Close command
  const sendClose = async (index: string, action: string, posIdx = -1) => {
    const key = `${index}-${action}-${posIdx}`;
    setClosing(key);
    try {
      await fetch(`${API_BASE}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index, action, position_index: posIdx }),
      });
    } catch {}
    setTimeout(() => setClosing(""), 2000);
  };

  const indices   = state?.indices ?? {};
  const allPos    = state?.all_positions ?? {};
  const history   = state?.trade_history ?? [];

  // Aggregate totals
  const totalUnrealized = Object.values(indices).reduce((s, i) => s + (i.pnl?.unrealized ?? 0), 0);
  const totalToday      = Object.values(indices).reduce((s, i) => s + (i.pnl?.today_realized ?? 0), 0);
  const totalRealized   = Object.values(indices).reduce((s, i) => s + (i.pnl?.realized ?? 0), 0);
  const totalTrades     = Object.values(indices).reduce((s, i) => s + (i.pnl?.today_trades ?? 0), 0);

  // History stats
  const pnls    = history.map(h => h.pnl);
  const wins    = pnls.filter(p => p > 0).length;
  const losses  = pnls.filter(p => p < 0).length;
  const netPnl  = pnls.reduce((s, p) => s + p, 0);
  const winRate = pnls.length > 0 ? wins / pnls.length : 0;
  const grossW  = pnls.filter(p => p > 0).reduce((s, p) => s + p, 0);
  const grossL  = Math.abs(pnls.filter(p => p < 0).reduce((s, p) => s + p, 0));
  const pf      = grossL > 0 ? grossW / grossL : null;

  // Monthly P&L for chart
  const monthly: Record<string, number> = {};
  history.forEach(h => {
    const m = (h.exit_time ?? "").slice(0, 7);
    if (m) monthly[m] = (monthly[m] ?? 0) + h.pnl;
  });
  const monthlyData = Object.entries(monthly).sort().map(([m, v]) => ({ month: m, pnl: v }));

  // Today's trades
  const today = new Date().toISOString().slice(0, 10);
  const todayTrades = history.filter(h => (h.exit_time ?? "").startsWith(today));

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-6">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-cyan-400">📈 Algo Trading Bot</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {lastUpdate ? `Updated ${new Date(lastUpdate).toLocaleTimeString("en-IN")}` : "Connecting…"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`flex items-center gap-1.5 text-sm px-3 py-1 rounded-full border ${
            connected ? "border-emerald-700 text-emerald-400 bg-emerald-950"
                      : "border-red-700 text-red-400 bg-red-950"
          }`}>
            <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-red-500"}`} />
            {connected ? "Live" : "Reconnecting…"}
          </span>
          <div className="flex rounded-lg overflow-hidden border border-gray-700">
            {(["live", "history"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-4 py-1.5 text-sm font-medium transition-colors ${
                  tab === t ? "bg-cyan-700 text-white" : "bg-gray-900 text-gray-400 hover:text-white"
                }`}>
                {t === "live" ? "Live" : "History"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {tab === "live" && (
        <>
          {/* ── Overall P&L bar ── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Metric label="Unrealized P&L" value={rs(totalUnrealized, true)}
                    color={clr(totalUnrealized)} />
            <Metric label="Today Realized" value={rs(totalToday, true)}
                    color={clr(totalToday)} sub={`${totalTrades} trade${totalTrades !== 1 ? "s" : ""}`} />
            <Metric label="Session Realized" value={rs(totalRealized, true)}
                    color={clr(totalRealized)} />
            <Metric label="Session Total" value={rs(totalRealized + totalUnrealized, true)}
                    color={clr(totalRealized + totalUnrealized)} />
          </div>

          {/* ── Index cards ── */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            {INDICES.map(idx => {
              const s   = indices[idx];
              const pnl = s?.pnl;
              return (
                <div key={idx} className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-lg font-bold text-white">{idx}</span>
                    <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${regimeBadge(s?.regime ?? null)}`}>
                      {regimeIcon(s?.regime ?? null)} {s?.regime ?? "—"}
                    </span>
                  </div>
                  <p className="text-3xl font-bold text-cyan-300 mb-4">
                    {s?.spot ? s.spot.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}
                  </p>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div className="bg-gray-800 rounded-lg p-2">
                      <p className="text-xs text-gray-500">Unrealized</p>
                      <p className={`font-bold ${clr(pnl?.unrealized)}`}>{rs(pnl?.unrealized, true)}</p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-2">
                      <p className="text-xs text-gray-500">Today P&L</p>
                      <p className={`font-bold ${clr(pnl?.today_realized)}`}>{rs(pnl?.today_realized, true)}</p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-2">
                      <p className="text-xs text-gray-500">Max Profit</p>
                      <p className="font-bold text-emerald-400">{rs(pnl?.max_profit)}</p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-2">
                      <p className="text-xs text-gray-500">Max Loss</p>
                      <p className="font-bold text-red-400">{rs(pnl?.max_loss)}</p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-2">
                      <p className="text-xs text-gray-500">Net Credit</p>
                      <p className="font-bold text-cyan-300">{rs(pnl?.net_credit)}</p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-2">
                      <p className="text-xs text-gray-500">Open Pos</p>
                      <p className="font-bold text-white">{pnl?.open_positions ?? "—"}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* ── Open Positions ── */}
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 mb-6">
            <h2 className="text-lg font-bold text-white mb-4">Open Positions</h2>
            {Object.entries(allPos).flatMap(([idx, positions]) =>
              (positions ?? []).filter(p => p.open).map((pos, pi) => (
                <div key={`${idx}-${pi}`} className="mb-6 last:mb-0">
                  {/* Position header */}
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2">
                      <span className="bg-cyan-900 text-cyan-300 text-xs font-bold px-2 py-0.5 rounded">{idx}</span>
                      <span className="font-bold text-white">{pos.strategy}</span>
                      <span className="text-xs text-gray-500">{pos.entry_time}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`font-bold ${clr(pos.unrealized)}`}>{rs(pos.unrealized, true)}</span>
                      <button
                        onClick={() => sendClose(idx, "close_position", pi)}
                        disabled={closing === `${idx}-close_position-${pi}`}
                        className="text-xs bg-red-900 hover:bg-red-700 text-red-200 px-3 py-1 rounded-lg border border-red-700 transition-colors disabled:opacity-50">
                        {closing === `${idx}-close_position-${pi}` ? "Closing…" : "Close"}
                      </button>
                    </div>
                  </div>
                  {/* Risk metrics */}
                  <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-3">
                    {[
                      ["Max Profit", rs(pos.max_profit), "text-emerald-400"],
                      ["Max Loss",   rs(pos.max_loss),   "text-red-400"],
                      ["Net Credit", rs(pos.net_credit), "text-cyan-300"],
                      ["R/R", pos.max_loss > 0 ? `1:${(pos.max_profit / pos.max_loss).toFixed(2)}` : "—", "text-white"],
                      ["Margin Req", rs(pos.margin_info?.margin_required ?? pos.max_loss), "text-yellow-300"],
                      ["Peak Margin", rs(pos.margin_info?.peak_margin_est ?? 0), "text-orange-300"],
                    ].map(([l, v, c]) => (
                      <div key={l as string} className="bg-gray-800 rounded-lg p-2 text-center">
                        <p className="text-xs text-gray-500">{l}</p>
                        <p className={`text-sm font-bold ${c}`}>{v}</p>
                      </div>
                    ))}
                  </div>
                  {/* Legs table */}
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-xs text-gray-500 border-b border-gray-800">
                          <th className="text-left py-1 pr-3">Side</th>
                          <th className="text-left py-1 pr-3">Type</th>
                          <th className="text-right py-1 pr-3">Strike</th>
                          <th className="text-right py-1 pr-3">Entry</th>
                          <th className="text-right py-1 pr-3">LTP</th>
                          <th className="text-right py-1 pr-3">Change</th>
                          <th className="text-right py-1 pr-3">Qty</th>
                          <th className="text-right py-1">Leg P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pos.legs.map((leg, li) => {
                          const chg = leg.ltp > 0 ? leg.ltp - leg.price : 0;
                          return (
                            <tr key={li} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                              <td className={`py-1.5 pr-3 font-bold ${leg.side === "SELL" ? "text-red-400" : "text-emerald-400"}`}>
                                {leg.side}
                              </td>
                              <td className="py-1.5 pr-3 text-gray-300">{leg.type}</td>
                              <td className="py-1.5 pr-3 text-right font-mono text-white">
                                {leg.strike.toLocaleString("en-IN")}
                              </td>
                              <td className="py-1.5 pr-3 text-right font-mono text-gray-300">
                                ₹{leg.price.toFixed(2)}
                              </td>
                              <td className="py-1.5 pr-3 text-right font-mono text-cyan-300">
                                {leg.ltp > 0 ? `₹${leg.ltp.toFixed(2)}` : "—"}
                              </td>
                              <td className={`py-1.5 pr-3 text-right font-mono ${clr(chg)}`}>
                                {leg.ltp > 0 ? `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}` : "—"}
                              </td>
                              <td className="py-1.5 pr-3 text-right text-gray-400">{leg.qty}</td>
                              <td className={`py-1.5 text-right font-bold ${clr(leg.unrealized_pnl)}`}>
                                {rs(leg.unrealized_pnl, true)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))
            ).length === 0 ? (
              <p className="text-gray-500 text-center py-8">No open positions — bot is monitoring the market</p>
            ) : null}

            {/* Close All button */}
            {Object.entries(allPos).some(([, ps]) => (ps ?? []).some(p => p.open)) && (
              <div className="mt-4 pt-4 border-t border-gray-800 flex justify-end">
                {INDICES.filter(idx => (allPos[idx] ?? []).some(p => p.open)).map(idx => (
                  <button key={idx}
                    onClick={() => sendClose(idx, "close_all")}
                    disabled={closing === `${idx}-close_all--1`}
                    className="mr-2 text-sm bg-red-900 hover:bg-red-700 text-red-200 px-4 py-2 rounded-lg border border-red-700 transition-colors disabled:opacity-50">
                    {closing === `${idx}-close_all--1` ? "Closing…" : `Close All ${idx}`}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* ── Today's Trades ── */}
          {todayTrades.length > 0 && (
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 mb-6">
              <h2 className="text-lg font-bold text-white mb-4">
                Today&apos;s Trades
                <span className="ml-2 text-sm font-normal text-gray-500">
                  {todayTrades.filter(t => t.pnl > 0).length}W /
                  {todayTrades.filter(t => t.pnl < 0).length}L
                </span>
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-gray-800">
                      <th className="text-left py-1 pr-3">Index</th>
                      <th className="text-left py-1 pr-3">Strategy</th>
                      <th className="text-left py-1 pr-3">Entry</th>
                      <th className="text-left py-1 pr-3">Exit</th>
                      <th className="text-left py-1 pr-3">Reason</th>
                      <th className="text-right py-1 pr-3">Net Credit</th>
                      <th className="text-right py-1 pr-3">Max Profit</th>
                      <th className="text-right py-1 pr-3">Max Loss</th>
                      <th className="text-right py-1">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {todayTrades.map((t, i) => (
                      <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                        <td className="py-1.5 pr-3 text-cyan-300 font-bold">{t.index}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.strategy}</td>
                        <td className="py-1.5 pr-3 text-gray-500 text-xs">{t.entry_time?.slice(11, 16)}</td>
                        <td className="py-1.5 pr-3 text-gray-500 text-xs">{t.exit_time?.slice(11, 16)}</td>
                        <td className="py-1.5 pr-3">
                          <span className="text-xs bg-gray-800 px-1.5 py-0.5 rounded text-gray-400">
                            {t.exit_reason?.replace(/_/g, " ")}
                          </span>
                        </td>
                        <td className="py-1.5 pr-3 text-right text-cyan-300">{rs(t.net_credit)}</td>
                        <td className="py-1.5 pr-3 text-right text-emerald-400">{rs(t.max_profit)}</td>
                        <td className="py-1.5 pr-3 text-right text-red-400">{rs(t.max_loss)}</td>
                        <td className={`py-1.5 text-right font-bold ${clr(t.pnl)}`}>{rs(t.pnl, true)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {tab === "history" && (
        <>
          {/* ── Stats ── */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
            <Metric label="Total Trades" value={String(pnls.length)} />
            <Metric label="Win Rate" value={pct(winRate)} color={winRate >= 0.5 ? "text-emerald-400" : "text-red-400"} />
            <Metric label="Net P&L" value={rs(netPnl, true)} color={clr(netPnl)} />
            <Metric label="Profit Factor" value={pf ? pf.toFixed(2) : "—"} color={pf && pf >= 1 ? "text-emerald-400" : "text-red-400"} />
            <Metric label="Avg Trade" value={pnls.length ? rs(netPnl / pnls.length, true) : "—"} color={clr(netPnl)} />
          </div>
          <div className="grid grid-cols-3 gap-3 mb-6">
            <Metric label="Winners" value={String(wins)} color="text-emerald-400" sub={`₹${grossW.toLocaleString("en-IN", {maximumFractionDigits:0})} gross`} />
            <Metric label="Losers"  value={String(losses)} color="text-red-400" sub={`₹${grossL.toLocaleString("en-IN", {maximumFractionDigits:0})} gross`} />
            <Metric label="Best Trade" value={rs(pnls.length ? Math.max(...pnls) : 0, true)} color="text-emerald-400" />
          </div>

          {/* ── Monthly P&L chart ── */}
          {monthlyData.length > 0 && (
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 mb-6">
              <h2 className="text-lg font-bold text-white mb-4">Monthly P&L</h2>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={monthlyData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="month" tick={{ fill: "#6b7280", fontSize: 11 }} />
                  <YAxis tick={{ fill: "#6b7280", fontSize: 11 }}
                    tickFormatter={v => `₹${(v/1000).toFixed(0)}k`} />
                  <Tooltip
                    contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
                    formatter={(v: number) => [rs(v, true), "P&L"]}
                  />
                  <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                    {monthlyData.map((d, i) => (
                      <Cell key={i} fill={d.pnl >= 0 ? "#10b981" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* ── Strategy breakdown ── */}
          {history.length > 0 && (() => {
            const byStrat: Record<string, { trades: number; wins: number; pnl: number }> = {};
            history.forEach(h => {
              if (!byStrat[h.strategy]) byStrat[h.strategy] = { trades: 0, wins: 0, pnl: 0 };
              byStrat[h.strategy].trades++;
              if (h.pnl > 0) byStrat[h.strategy].wins++;
              byStrat[h.strategy].pnl += h.pnl;
            });
            return (
              <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 mb-6">
                <h2 className="text-lg font-bold text-white mb-4">Strategy Accuracy</h2>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-gray-800">
                      <th className="text-left py-1 pr-3">Strategy</th>
                      <th className="text-right py-1 pr-3">Trades</th>
                      <th className="text-right py-1 pr-3">Wins</th>
                      <th className="text-right py-1 pr-3">Win Rate</th>
                      <th className="text-right py-1">Total P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(byStrat).map(([s, d]) => (
                      <tr key={s} className="border-b border-gray-800/50">
                        <td className="py-2 pr-3 font-bold text-cyan-300">{s}</td>
                        <td className="py-2 pr-3 text-right text-gray-300">{d.trades}</td>
                        <td className="py-2 pr-3 text-right text-emerald-400">{d.wins}</td>
                        <td className={`py-2 pr-3 text-right font-bold ${d.wins/d.trades >= 0.5 ? "text-emerald-400" : "text-red-400"}`}>
                          {(d.wins / d.trades * 100).toFixed(1)}%
                        </td>
                        <td className={`py-2 text-right font-bold ${clr(d.pnl)}`}>{rs(d.pnl, true)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })()}

          {/* ── Full trade log ── */}
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
            <h2 className="text-lg font-bold text-white mb-4">
              All Trades ({history.length})
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-800">
                    <th className="text-left py-1 pr-3">Date</th>
                    <th className="text-left py-1 pr-3">Index</th>
                    <th className="text-left py-1 pr-3">Strategy</th>
                    <th className="text-left py-1 pr-3">Reason</th>
                    <th className="text-right py-1 pr-3">Net Credit</th>
                    <th className="text-right py-1 pr-3">Max Profit</th>
                    <th className="text-right py-1 pr-3">Max Loss</th>
                    <th className="text-right py-1">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {[...history].reverse().map((t, i) => (
                    <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="py-1.5 pr-3 text-gray-500 text-xs">{t.exit_time?.slice(0, 16)}</td>
                      <td className="py-1.5 pr-3 text-cyan-300 font-bold">{t.index}</td>
                      <td className="py-1.5 pr-3 text-gray-300">{t.strategy}</td>
                      <td className="py-1.5 pr-3">
                        <span className="text-xs bg-gray-800 px-1.5 py-0.5 rounded text-gray-400">
                          {t.exit_reason?.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3 text-right text-cyan-300">{rs(t.net_credit)}</td>
                      <td className="py-1.5 pr-3 text-right text-emerald-400">{rs(t.max_profit)}</td>
                      <td className="py-1.5 pr-3 text-right text-red-400">{rs(t.max_loss)}</td>
                      <td className={`py-1.5 text-right font-bold ${clr(t.pnl)}`}>{rs(t.pnl, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <p className="mt-6 text-center text-xs text-gray-700">
        WebSocket live · REST fallback every 5s · {API_BASE}
      </p>
    </div>
  );
}
