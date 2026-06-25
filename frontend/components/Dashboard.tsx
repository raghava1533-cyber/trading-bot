"use client";

/**
 * Dashboard.tsx
 * Live trading bot dashboard — connects to the Render backend via WebSocket
 * and falls back to polling the REST /state endpoint.
 */

import { useEffect, useRef, useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────
interface PnL {
  realized: number;
  unrealized: number;
  total: number;
  open_positions: number;
}

interface IndexState {
  pnl: PnL | null;
  spot: number | null;
  regime: "BULL" | "BEAR" | "SIDE" | null;
}

type BotState = Record<string, IndexState>;

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS_BASE  = API_BASE.replace(/^http/, "ws");
const INDICES  = ["NIFTY", "BANKNIFTY", "SENSEX"];

// ── Helpers ───────────────────────────────────────────────────────────────────
function regimeColor(regime: string | null) {
  if (regime === "BULL") return "text-green-400";
  if (regime === "BEAR") return "text-red-400";
  if (regime === "SIDE") return "text-yellow-400";
  return "text-gray-400";
}

function pnlColor(val: number | undefined) {
  if (val === undefined || val === null) return "text-gray-400";
  return val >= 0 ? "text-green-400" : "text-red-400";
}

function fmt(val: number | null | undefined, prefix = "₹") {
  if (val === null || val === undefined) return "—";
  return `${prefix}${val.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [state, setState]       = useState<BotState>({});
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // ── WebSocket connection ───────────────────────────────────────────────────
  useEffect(() => {
    function connect() {
      const ws = new WebSocket(`${WS_BASE}/ws`);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        console.log("WS connected");
      };

      ws.onmessage = (event) => {
        try {
          const data: BotState = JSON.parse(event.data);
          setState(data);
          setLastUpdate(new Date());
        } catch (e) {
          console.error("WS parse error", e);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        console.log("WS disconnected — retrying in 3s");
        setTimeout(connect, 3000);
      };

      ws.onerror = (err) => {
        console.error("WS error", err);
        ws.close();
      };
    }

    connect();
    return () => wsRef.current?.close();
  }, []);

  // ── REST polling fallback (every 5s when WS is down) ──────────────────────
  useEffect(() => {
    if (connected) return;
    const id = setInterval(async () => {
      try {
        const res  = await fetch(`${API_BASE}/state`);
        const data = await res.json();
        setState(data);
        setLastUpdate(new Date());
      } catch (e) {
        console.error("Poll error", e);
      }
    }, 5000);
    return () => clearInterval(id);
  }, [connected]);

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 font-mono">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-cyan-400">
          📈 Algo Trading Bot — Live Dashboard
        </h1>
        <div className="flex items-center gap-2 text-sm">
          <span
            className={`w-2 h-2 rounded-full ${connected ? "bg-green-400" : "bg-red-500"}`}
          />
          <span className={connected ? "text-green-400" : "text-red-400"}>
            {connected ? "Live" : "Reconnecting…"}
          </span>
          {lastUpdate && (
            <span className="text-gray-500 ml-2">
              Updated {lastUpdate.toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      {/* Index cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {INDICES.map((idx) => {
          const s = state[idx];
          return (
            <div
              key={idx}
              className="bg-gray-900 border border-gray-700 rounded-xl p-5 shadow-lg"
            >
              {/* Index name + regime */}
              <div className="flex items-center justify-between mb-3">
                <span className="text-lg font-bold text-white">{idx}</span>
                <span
                  className={`text-sm font-semibold px-2 py-0.5 rounded ${regimeColor(s?.regime ?? null)} bg-gray-800`}
                >
                  {s?.regime ?? "—"}
                </span>
              </div>

              {/* Spot */}
              <div className="mb-4">
                <p className="text-xs text-gray-500 uppercase tracking-wider">Spot</p>
                <p className="text-2xl font-bold text-cyan-300">
                  {s?.spot ? s.spot.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}
                </p>
              </div>

              {/* PnL grid */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-xs text-gray-500">Realized</p>
                  <p className={`font-semibold ${pnlColor(s?.pnl?.realized)}`}>
                    {fmt(s?.pnl?.realized)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Unrealized</p>
                  <p className={`font-semibold ${pnlColor(s?.pnl?.unrealized)}`}>
                    {fmt(s?.pnl?.unrealized)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Total P&amp;L</p>
                  <p className={`font-bold text-base ${pnlColor(s?.pnl?.total)}`}>
                    {fmt(s?.pnl?.total)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Open Positions</p>
                  <p className="font-semibold text-white">
                    {s?.pnl?.open_positions ?? "—"}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <p className="mt-8 text-center text-xs text-gray-600">
        Data refreshes every second via WebSocket · Fallback REST polling every 5s
      </p>
    </div>
  );
}
