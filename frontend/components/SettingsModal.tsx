"use client";
import { useEffect, useState } from "react";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

interface SettingsData {
  api_key: string;
  api_secret_set: boolean;
  api_secret_hint: string;
  token_valid: boolean;
  token_hint: string;
  user_name: string;
  login_url: string;
}

interface Props {
  onClose: () => void;
}

export default function SettingsModal({ onClose }: Props) {
  const [data, setData]         = useState<SettingsData | null>(null);
  const [apiKey, setApiKey]     = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [token, setToken]       = useState("");
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState<{ text: string; ok: boolean } | null>(null);
  const [loading, setLoading]   = useState(true);

  // Load current settings on open
  useEffect(() => {
    fetch(API_BASE + "/settings")
      .then(r => r.json())
      .then(d => {
        setData(d);
        setApiKey(d.api_key ?? "");
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const r = await fetch(API_BASE + "/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, access_token: token }),
      });
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
        // Refresh status
        const fresh = await fetch(API_BASE + "/settings").then(r2 => r2.json());
        setData(fresh);
        setApiSecret("");  // clear secret field after save
        setToken("");      // clear token field after save
      } else {
        setMsg({ text: "Error: " + (result.error ?? "unknown"), ok: false });
      }
    } catch (e) {
      setMsg({ text: "Network error - is Render running?", ok: false });
    }
    setSaving(false);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(0,0,0,0.75)" }}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-gray-800">
          <div>
            <h2 className="text-lg font-bold text-white">Bot Settings</h2>
            <p className="text-xs text-gray-500 mt-0.5">Upstox credentials stored in Redis</p>
          </div>
          <button onClick={onClose}
            className="text-gray-500 hover:text-white text-2xl leading-none px-2">&times;</button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : (
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
                    ? ("Valid - " + (data.user_name || data.token_hint))
                    : "Expired / Not set"}
                </span>
              </div>
            </div>

            {/* Input fields */}
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  UPSTOX_API_KEY
                  <span className="ml-2 text-gray-600">(from developer.upstox.com)</span>
                </label>
                <input
                  type="text"
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  placeholder="e.g. xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder-gray-600 focus:outline-none focus:border-cyan-600"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  UPSTOX_API_SECRET
                  <span className="ml-2 text-gray-600">(leave blank to keep existing)</span>
                </label>
                <input
                  type="password"
                  value={apiSecret}
                  onChange={e => setApiSecret(e.target.value)}
                  placeholder="Leave blank to keep existing secret"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder-gray-600 focus:outline-none focus:border-cyan-600"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  UPSTOX_ACCESS_TOKEN
                  <span className="ml-2 text-gray-600">(paste if you have it, or use Login below)</span>
                </label>
                <textarea
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  placeholder="eyJ0eXAiOiJKV1QiLCJr... (paste full token)"
                  rows={3}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs text-white font-mono placeholder-gray-600 focus:outline-none focus:border-cyan-600 resize-none"
                />
              </div>
            </div>

            {/* Message */}
            {msg && (
              <div className={`rounded-lg px-4 py-2 text-sm ${msg.ok ? "bg-emerald-900 text-emerald-300 border border-emerald-700" : "bg-red-900 text-red-300 border border-red-700"}`}>
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
              Credentials saved to Redis on Render. Never stored in browser.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
