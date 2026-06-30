"use client";
import { useEffect, useState, useCallback } from "react";

const RENDER_URL = "https://trading-bot-av9x.onrender.com";
const API_BASE   = (process.env.NEXT_PUBLIC_API_URL ?? RENDER_URL).replace(/\/$/, "");

interface SettingsData {
  api_key: string;
  api_secret_set: boolean;
  api_secret_hint: string;
  token_valid: boolean;
  token_hint: string;
  user_name: string;
  login_url: string;
}

interface Props { onClose: () => void; }

type ConnState = "idle" | "waking" | "connected" | "error";

export default function SettingsModal({ onClose }: Props) {
  const [conn, setConn]         = useState<ConnState>("idle");
  const [connMsg, setConnMsg]   = useState("");
  const [data, setData]         = useState<SettingsData | null>(null);
  const [apiKey, setApiKey]     = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [token, setToken]       = useState("");
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState<{ text: string; ok: boolean } | null>(null);

  // Wake Render + load settings with retry
  const loadSettings = useCallback(async () => {
    setConn("waking");
    setConnMsg("Connecting to Render...");
    setMsg(null);

    // Try up to 4 times with increasing delay (Render free tier wakes in ~30s)
    const delays = [0, 5000, 10000, 15000];
    for (let attempt = 0; attempt < delays.length; attempt++) {
      if (delays[attempt] > 0) {
        const secs = delays[attempt] / 1000;
        setConnMsg(`Render is waking up... retrying in ${secs}s (attempt ${attempt + 1}/4)`);
        await new Promise(r => setTimeout(r, delays[attempt]));
      }
      try {
        setConnMsg(`Connecting... (attempt ${attempt + 1}/4)`);
        const r = await fetch(API_BASE + "/settings", { signal: AbortSignal.timeout(12000) });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d: SettingsData = await r.json();
        setData(d);
        setApiKey(d.api_key ?? "");
        setConn("connected");
        setConnMsg("");
        return;
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setConnMsg(`Attempt ${attempt + 1} failed: ${msg}`);
      }
    }
    setConn("error");
    setConnMsg("Cannot reach Render after 4 attempts. Check if service is deployed.");
  }, []);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const r = await fetch(API_BASE + "/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, access_token: token }),
        signal: AbortSignal.timeout(15000),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const result = await r.json();
      if (result.ok) {
        const ts = result.token_status;
        if (ts?.valid) {
          setMsg({ text: "Saved! Token valid. Logged in as: " + (ts.name || "Upstox User"), ok: true });
        } else if (token) {
          setMsg({ text: "Saved but token invalid: " + (ts?.reason ?? "unknown"), ok: false });
        } else {
          setMsg({ text: "Credentials saved. Use Login button to get token.", ok: true });
        }
        const fresh = await fetch(API_BASE + "/settings").then(r2 => r2.json());
        setData(fresh);
        setApiSecret("");
        setToken("");
      } else {
        setMsg({ text: "Error: " + (result.error ?? "unknown"), ok: false });
      }
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? e.message : String(e);
      setMsg({ text: "Network error: " + errMsg, ok: false });
    }
    setSaving(false);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(0,0,0,0.8)" }}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-gray-800">
          <div>
            <h2 className="text-lg font-bold text-white">Bot Settings</h2>
            <p className="text-xs text-gray-500 mt-0.5">{API_BASE}</p>
          </div>
          <button onClick={onClose}
            className="text-gray-500 hover:text-white text-2xl leading-none px-2">&times;</button>
        </div>

        {/* Connection states */}
        {conn === "waking" && (
          <div className="p-8 text-center space-y-3">
            <div className="flex justify-center">
              <div className="w-8 h-8 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
            </div>
            <p className="text-sm text-gray-400">{connMsg}</p>
            <p className="text-xs text-gray-600">
              Render free tier sleeps after 15 min of inactivity.<br />
              First connection takes up to 30 seconds.
            </p>
          </div>
        )}

        {conn === "error" && (
          <div className="p-6 space-y-4">
            <div className="bg-red-950 border border-red-800 rounded-xl p-4">
              <p className="text-red-400 font-bold text-sm mb-1">Cannot reach Render</p>
              <p className="text-red-300 text-xs">{connMsg}</p>
            </div>
            <div className="bg-gray-800 rounded-xl p-4 text-xs text-gray-400 space-y-1">
              <p className="font-bold text-gray-300 mb-2">Checklist:</p>
              <p>1. Is the Render service deployed? Check render.com dashboard</p>
              <p>2. Is the service name <span className="font-mono text-cyan-400">trading-bot-api</span>?</p>
              <p>3. Visit <a href={RENDER_URL + "/health"} target="_blank" rel="noreferrer"
                className="text-cyan-400 underline">{RENDER_URL}/health</a> directly</p>
              <p>4. Check Render logs for startup errors</p>
            </div>
            <button onClick={loadSettings}
              className="w-full bg-cyan-800 hover:bg-cyan-700 text-white font-bold py-2.5 rounded-xl">
              Retry Connection
            </button>
          </div>
        )}

        {conn === "connected" && (
          <div className="p-5 space-y-4">
            {/* Current status */}
            <div className="bg-gray-800 rounded-xl p-4 space-y-2">
              <p className="text-xs text-gray-400 uppercase tracking-wider font-bold mb-2">Current Status</p>
              <div className="flex items-center justify-between">
                <span className="text-sm text-gray-400">API Key</span>
                <span className="text-sm font-mono text-cyan-300">
                  {data?.api_key ? data.api_key.slice(0, 8) + "****" : "Not set"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-gray-400">API Secret</span>
                <span className="text-sm font-mono text-cyan-300">
                  {data?.api_secret_set ? (data.api_secret_hint || "****") : "Not set"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-gray-400">Token</span>
                <span className={`text-sm font-bold ${data?.token_valid ? "text-emerald-400" : "text-red-400"}`}>
                  {data?.token_valid
                    ? "Valid - " + (data.user_name || data.token_hint)
                    : "Expired / Not set"}
                </span>
              </div>
            </div>

            {/* Input fields */}
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  UPSTOX_API_KEY
                  <span className="ml-2 text-gray-600">from developer.upstox.com</span>
                </label>
                <input type="text" value={apiKey} onChange={e => setApiKey(e.target.value)}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder-gray-600 focus:outline-none focus:border-cyan-600" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  UPSTOX_API_SECRET
                  <span className="ml-2 text-gray-600">leave blank to keep existing</span>
                </label>
                <input type="password" value={apiSecret} onChange={e => setApiSecret(e.target.value)}
                  placeholder="Leave blank to keep existing"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder-gray-600 focus:outline-none focus:border-cyan-600" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  UPSTOX_ACCESS_TOKEN
                  <span className="ml-2 text-gray-600">paste token OR use Login button below</span>
                </label>
                <textarea value={token} onChange={e => setToken(e.target.value)}
                  placeholder="eyJ0eXAiOiJKV1QiLCJr... (paste full token here)"
                  rows={3}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs text-white font-mono placeholder-gray-600 focus:outline-none focus:border-cyan-600 resize-none" />
              </div>
            </div>

            {/* Message */}
            {msg && (
              <div className={`rounded-lg px-4 py-2 text-sm ${
                msg.ok
                  ? "bg-emerald-900 text-emerald-300 border border-emerald-700"
                  : "bg-red-900 text-red-300 border border-red-700"
              }`}>
                {msg.text}
              </div>
            )}

            {/* Buttons */}
            <div className="flex gap-3 pt-1">
              <button onClick={save} disabled={saving}
                className="flex-1 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 text-white font-bold py-2.5 rounded-xl transition-colors">
                {saving ? "Saving..." : "Save Credentials"}
              </button>
              <a href={API_BASE + "/auth"} target="_blank" rel="noreferrer"
                className="flex-1 text-center bg-orange-700 hover:bg-orange-600 text-white font-bold py-2.5 rounded-xl transition-colors">
                Login with Upstox
              </a>
            </div>
            <p className="text-xs text-gray-600 text-center">
              Saved to Redis on Render. Never stored in browser or git.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}