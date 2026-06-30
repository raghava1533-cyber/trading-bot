"use client";
import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "https://trading-bot-av9x.onrender.com").replace(/\/$/, "");

// ── Config schema: all editable settings grouped by category ─────────────────
const CONFIG_SCHEMA = [
  {
    group: "Trading Mode",
    icon: "🎮",
    fields: [
      { key: "DRY_RUN",         label: "Dry Run (Paper Trading)", type: "bool",   default: "true",  hint: "true = simulate only, no real orders placed" },
      { key: "BOT_ENABLED",     label: "Bot Auto-Start on Deploy", type: "bool",  default: "true",  hint: "Start bot automatically when Render restarts" },
      { key: "ACTIVE_INDICES",  label: "Active Indices",           type: "text",  default: "NIFTY", hint: "Comma separated: NIFTY, BANKNIFTY, SENSEX" },
    ],
  },
  {
    group: "Risk Management",
    icon: "🛡️",
    fields: [
      { key: "STOP_LOSS",         label: "Stop Loss (₹)",          type: "number", default: "-1500", hint: "Max loss per position before auto-close (negative)" },
      { key: "TARGET_PROFIT",     label: "Target Profit (₹)",      type: "number", default: "1000",  hint: "Profit target per position before auto-close" },
      { key: "MAX_TRADES_PER_DAY",label: "Max Trades Per Day",     type: "number", default: "3",     hint: "Maximum new positions to open per day" },
      { key: "MIN_CREDIT_RATIO",  label: "Min Credit Ratio",       type: "number", default: "0.25",  hint: "Min credit / spread width (0.25 = 25% of spread)" },
      { key: "SPREAD_WIDTH_POINTS",label:"Spread Width (points)",  type: "number", default: "200",   hint: "Distance between strikes in points (e.g. 200)" },
    ],
  },
  {
    group: "Strategy",
    icon: "📊",
    fields: [
      { key: "TARGET_DELTA",              label: "Target Delta",           type: "number", default: "0.30",  hint: "Delta of short strike (0.30 = 30 delta)" },
      { key: "DELTA_HEDGE_THRESHOLD",     label: "Delta Hedge Threshold",  type: "number", default: "0.05",  hint: "Rehedge when net delta exceeds this" },
      { key: "REGIME_SIDE_VOL_THRESHOLD", label: "Sideways Vol Threshold", type: "number", default: "0.007", hint: "Daily vol below this = SIDEWAYS regime" },
      { key: "MIN_TIME_TO_EXPIRY_DAYS",   label: "Min Days to Expiry",     type: "number", default: "1",     hint: "Skip expiries closer than this many days" },
    ],
  },
  {
    group: "Timing",
    icon: "⏰",
    fields: [
      { key: "POLL_INTERVAL_SECONDS",   label: "Poll Interval (sec)",    type: "number", default: "60",   hint: "How often bot checks market (seconds)" },
      { key: "TRADE_COOLDOWN_SECONDS",  label: "Trade Cooldown (sec)",   type: "number", default: "900",  hint: "Min gap between trades (900 = 15 min)" },
      { key: "MARKET_OPEN_TIME",        label: "Market Open Time",       type: "text",   default: "09:15",hint: "Format: HH:MM (IST)" },
      { key: "MARKET_CLOSE_TIME",       label: "Market Close Time",      type: "text",   default: "15:30",hint: "Format: HH:MM (IST)" },
    ],
  },
  {
    group: "Backtest",
    icon: "🔬",
    fields: [
      { key: "BACKTEST_DAYS",             label: "Backtest Days",          type: "number", default: "365",    hint: "Historical days to backtest" },
      { key: "BACKTEST_INITIAL_CAPITAL",  label: "Initial Capital (₹)",    type: "number", default: "500000", hint: "Starting capital for backtest" },
      { key: "BACKTEST_STOP_LOSS",        label: "Backtest Stop Loss (₹)", type: "number", default: "-1500",  hint: "Stop loss used in backtest" },
      { key: "BACKTEST_TARGET_PROFIT",    label: "Backtest Target (₹)",    type: "number", default: "1000",   hint: "Profit target used in backtest" },
    ],
  },
];

// ── Train Model Panel ─────────────────────────────────────────────────────────
function TrainPanel() {
  const [years, setYears]       = useState(3);
  const [indices, setIndices]   = useState(["NIFTY", "BANKNIFTY", "SENSEX"]);
  const [status, setStatus]     = useState<{ running: boolean; message: string; progress: string; log: string[] } | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(API_BASE + "/train/status");
      const d = await r.json();
      setStatus(d);
      if (!d.running && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchStatus();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchStatus]);

  const startTraining = async () => {
    setStarting(true);
    try {
      await fetch(API_BASE + "/train/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ years, indices }),
      });
      pollRef.current = setInterval(fetchStatus, 2000);
      await fetchStatus();
    } catch {}
    setStarting(false);
  };

  const stopTraining = async () => {
    await fetch(API_BASE + "/train/stop", { method: "POST" });
    await fetchStatus();
  };

  const toggleIndex = (idx: string) =>
    setIndices(prev => prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xl">🧠</span>
        <h3 className="text-base font-bold text-white">Train Regime Model</h3>
        <span className="text-xs text-gray-500 ml-1">(XGBoost - predicts BULL/BEAR/SIDE)</span>
      </div>

      {/* Options */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Years of Data</label>
          <select value={years} onChange={e => setYears(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-cyan-600">
            {[1,2,3,5,7,10].map(y => <option key={y} value={y}>{y} year{y>1?"s":""}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Indices</label>
          <div className="flex gap-2 mt-1">
            {["NIFTY","BANKNIFTY","SENSEX"].map(idx => (
              <button key={idx} onClick={() => toggleIndex(idx)}
                className={`text-xs px-2.5 py-1 rounded-lg border font-bold transition-colors ${
                  indices.includes(idx)
                    ? "bg-cyan-900 border-cyan-700 text-cyan-300"
                    : "bg-gray-800 border-gray-700 text-gray-500"
                }`}>
                {idx}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Status bar */}
      {status && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className={`text-sm font-bold ${status.running ? "text-yellow-400" : "text-gray-400"}`}>
              {status.running ? "⟳ " + status.message : status.message}
            </span>
            {status.running && (
              <span className="text-xs text-cyan-400 font-mono">{status.progress}</span>
            )}
          </div>
          {status.running && status.progress && (
            <div className="w-full bg-gray-800 rounded-full h-1.5">
              <div className="bg-cyan-500 h-1.5 rounded-full transition-all"
                style={{ width: status.progress }} />
            </div>
          )}
          {status.log && status.log.length > 0 && (
            <div className="bg-gray-950 rounded-lg p-3 max-h-36 overflow-y-auto">
              {status.log.map((line, i) => (
                <p key={i} className="text-xs font-mono text-gray-400 leading-5">{line}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Buttons */}
      <div className="flex gap-3">
        <button onClick={startTraining}
          disabled={starting || status?.running || indices.length === 0}
          className="flex-1 bg-cyan-800 hover:bg-cyan-700 disabled:opacity-40 text-white font-bold py-2.5 rounded-xl transition-colors text-sm">
          {starting ? "Starting..." : status?.running ? "Training..." : "Start Training"}
        </button>
        {status?.running && (
          <button onClick={stopTraining}
            className="px-5 bg-red-900 hover:bg-red-800 text-red-300 font-bold py-2.5 rounded-xl border border-red-700 text-sm">
            Cancel
          </button>
        )}
      </div>
      <p className="text-xs text-gray-600">
        Training takes 1-5 minutes. Bot must be restarted to use the new model.
      </p>
    </div>
  );
}

// ── Main Config Panel ─────────────────────────────────────────────────────────
interface Props { onClose: () => void; }

export default function ConfigPanel({ onClose }: Props) {
  const [loading, setLoading]   = useState(true);
  const [values, setValues]     = useState<Record<string, string>>({});
  const [sources, setSources]   = useState<Record<string, string>>({});
  const [dirty, setDirty]       = useState<Set<string>>(new Set());
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState<{ text: string; ok: boolean } | null>(null);
  const [tab, setTab]           = useState<"config" | "train">("config");
  const [connErr, setConnErr]   = useState("");

  useEffect(() => {
    fetch(API_BASE + "/config", { signal: AbortSignal.timeout(12000) })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          const vals: Record<string, string> = {};
          const srcs: Record<string, string> = {};
          for (const [k, v] of Object.entries(d.config as Record<string, { value: string; source: string }>)) {
            vals[k] = v.value ?? "";
            srcs[k] = v.source;
          }
          setValues(vals);
          setSources(srcs);
        }
        setLoading(false);
      })
      .catch(e => { setConnErr(String(e)); setLoading(false); });
  }, []);

  const set = (key: string, val: string) => {
    setValues(prev => ({ ...prev, [key]: val }));
    setDirty(prev => new Set(prev).add(key));
    setMsg(null);
  };

  const saveAll = async () => {
    if (dirty.size === 0) { setMsg({ text: "No changes to save", ok: false }); return; }
    setSaving(true);
    setMsg(null);
    const payload: Record<string, string> = {};
    dirty.forEach(k => { payload[k] = values[k]; });
    try {
      const r = await fetch(API_BASE + "/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (d.ok) {
        setMsg({ text: d.message + " Click Restart Bot to apply.", ok: true });
        setDirty(new Set());
        setSources(prev => { const n = { ...prev }; d.saved.forEach((k: string) => { n[k] = "redis"; }); return n; });
      } else {
        setMsg({ text: "Error: " + d.error, ok: false });
      }
    } catch (e) {
      setMsg({ text: "Network error: " + String(e), ok: false });
    }
    setSaving(false);
  };

  const resetKey = (key: string, def: string) => {
    set(key, def);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(0,0,0,0.85)" }}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col"
           style={{ maxHeight: "90vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-gray-800 flex-shrink-0">
          <div>
            <h2 className="text-lg font-bold text-white">Bot Configuration</h2>
            <p className="text-xs text-gray-500 mt-0.5">Changes saved to Redis — click Restart Bot to apply</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex rounded-lg overflow-hidden border border-gray-700">
              {(["config","train"] as const).map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={`px-4 py-1.5 text-sm font-medium transition-colors ${
                    tab === t ? "bg-cyan-700 text-white" : "bg-gray-900 text-gray-400 hover:text-white"
                  }`}>
                  {t === "config" ? "⚙️ Settings" : "🧠 Train Model"}
                </button>
              ))}
            </div>
            <button onClick={onClose}
              className="text-gray-500 hover:text-white text-2xl leading-none px-2">&times;</button>
          </div>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-5 space-y-5">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
            </div>
          )}
          {connErr && !loading && (
            <div className="bg-red-950 border border-red-800 rounded-xl p-4 text-red-300 text-sm">
              Cannot reach Render: {connErr}
            </div>
          )}

          {tab === "config" && !loading && !connErr && (
            <>
              {CONFIG_SCHEMA.map(group => (
                <div key={group.group} className="bg-gray-800/50 rounded-2xl p-4 space-y-3">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <span>{group.icon}</span>{group.group}
                  </h3>
                  {group.fields.map(field => {
                    const val = values[field.key] ?? field.default;
                    const isDirty = dirty.has(field.key);
                    const src = sources[field.key];
                    return (
                      <div key={field.key} className="grid grid-cols-5 gap-3 items-start">
                        <div className="col-span-2">
                          <p className="text-sm text-gray-300 font-medium">{field.label}</p>
                          <p className="text-xs text-gray-600 mt-0.5">{field.hint}</p>
                          {src && (
                            <span className={`text-xs px-1.5 py-0.5 rounded mt-1 inline-block ${
                              src === "redis" ? "bg-cyan-950 text-cyan-500" : "bg-gray-800 text-gray-600"
                            }`}>
                              {src === "redis" ? "custom" : "default"}
                            </span>
                          )}
                        </div>
                        <div className="col-span-2">
                          {field.type === "bool" ? (
                            <div className="flex gap-2 mt-1">
                              {["true","false"].map(opt => (
                                <button key={opt} onClick={() => set(field.key, opt)}
                                  className={`flex-1 py-1.5 rounded-lg text-sm font-bold border transition-colors ${
                                    val === opt
                                      ? opt === "true"
                                        ? "bg-emerald-900 border-emerald-700 text-emerald-300"
                                        : "bg-red-900 border-red-700 text-red-300"
                                      : "bg-gray-800 border-gray-700 text-gray-500 hover:text-white"
                                  }`}>
                                  {opt === "true" ? "ON" : "OFF"}
                                </button>
                              ))}
                            </div>
                          ) : (
                            <input
                              type={field.type === "number" ? "number" : "text"}
                              value={val}
                              onChange={e => set(field.key, e.target.value)}
                              className={`w-full bg-gray-800 border rounded-lg px-3 py-1.5 text-sm text-white font-mono focus:outline-none transition-colors ${
                                isDirty ? "border-yellow-600" : "border-gray-700 focus:border-cyan-600"
                              }`}
                            />
                          )}
                        </div>
                        <div className="flex items-center justify-end mt-1">
                          {isDirty && (
                            <button onClick={() => resetKey(field.key, field.default)}
                              className="text-xs text-gray-500 hover:text-gray-300 px-2 py-1 rounded border border-gray-700">
                              Reset
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </>
          )}

          {tab === "train" && !loading && <TrainPanel />}
        </div>

        {/* Footer */}
        {tab === "config" && (
          <div className="p-5 border-t border-gray-800 flex-shrink-0 space-y-3">
            {msg && (
              <div className={`rounded-lg px-4 py-2 text-sm ${
                msg.ok ? "bg-emerald-900 text-emerald-300 border border-emerald-700"
                       : "bg-red-900 text-red-300 border border-red-700"
              }`}>
                {msg.text}
              </div>
            )}
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-gray-600">
                {dirty.size > 0 ? `${dirty.size} unsaved change${dirty.size > 1 ? "s" : ""}` : "No unsaved changes"}
              </p>
              <div className="flex gap-3">
                <button onClick={onClose}
                  className="px-5 py-2 rounded-xl border border-gray-700 text-gray-400 hover:text-white text-sm">
                  Cancel
                </button>
                <button onClick={saveAll} disabled={saving || dirty.size === 0}
                  className="px-6 py-2 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 text-white font-bold rounded-xl text-sm transition-colors">
                  {saving ? "Saving..." : `Save ${dirty.size > 0 ? `(${dirty.size})` : ""}`}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}