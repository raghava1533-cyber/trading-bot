"""
api/server.py  -  FastAPI backend for Render FREE tier
────────────────────────────────────────────────────────
Endpoints:
  GET  /health          Render health check
  GET  /ping            UptimeRobot keep-alive
  GET  /auth            Open in browser → Upstox login page
  GET  /auth/callback   Upstox redirects here after login → saves token
  GET  /auth/status     Check if token is valid right now
  GET  /state           Full bot state (all indices)
  GET  /state/{index}   Single index state
  GET  /history         Trade history
  POST /close           Queue close command
  WS   /ws              WebSocket live feed
"""
import asyncio, datetime as _dt, json, logging, os, sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from infra.redis_bus import get_data, get_all_data, set_data

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


# ── WebSocket manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ── State builder ─────────────────────────────────────────────────────────────
def _build_state_payload() -> dict:
    try:
        from config import SETTINGS
        indices = list(SETTINGS.active_indices)
    except Exception:
        indices = ["NIFTY", "BANKNIFTY", "SENSEX"]

    state: dict = {"indices": {}}
    for idx in indices:
        pnl_raw    = get_data(f"pnl_{idx}")
        spot_raw   = get_data(f"spot_{idx}")
        regime_raw = get_data(f"regime_{idx}")
        state["indices"][idx] = {
            "pnl":    json.loads(pnl_raw) if pnl_raw else None,
            "spot":   float(spot_raw)     if spot_raw else None,
            "regime": regime_raw,
        }

    all_pos_raw = get_data("all_positions")
    if all_pos_raw:
        try:
            state["all_positions"] = json.loads(all_pos_raw)
        except Exception:
            state["all_positions"] = {}

    last_update = get_data("last_update")
    if last_update:
        state["last_update"] = last_update

    try:
        from execution.paper_engine import load_trade_history
        state["trade_history"] = load_trade_history()
    except Exception:
        state["trade_history"] = []

    return state


# ── Background tasks ──────────────────────────────────────────────────────────
# -- Bot task controller ------------------------------------------------------
_bot_task: asyncio.Task | None = None
_bot_status = {
    "running":    False,
    "start_time": None,
    "stop_time":  None,
    "message":    "Not started",
}


async def _run_bot():
    global _bot_status
    _bot_status["running"]    = True
    _bot_status["start_time"] = _dt.datetime.utcnow().isoformat() + "Z"
    _bot_status["message"]    = "Running"
    set_data("bot_status", "running")
    set_data("bot_start_time", _bot_status["start_time"])
    log.info("Bot task started")
    try:
        from main_async import main as bot_main
        await bot_main()
    except asyncio.CancelledError:
        log.info("Bot task cancelled (stop requested)")
        _bot_status["message"] = "Stopped by user"
    except Exception as exc:
        log.error(f"Bot crashed: {exc}", exc_info=True)
        _bot_status["message"] = f"Crashed: {exc}"
    finally:
        _bot_status["running"]   = False
        _bot_status["stop_time"] = _dt.datetime.utcnow().isoformat() + "Z"
        set_data("bot_status", "stopped")
        log.info("Bot task ended")


async def _start_bot():
    global _bot_task
    if _bot_task and not _bot_task.done():
        return {"ok": False, "message": "Bot already running"}
    _bot_task = asyncio.create_task(_run_bot())
    return {"ok": True, "message": "Bot started"}


async def _stop_bot():
    global _bot_task
    if not _bot_task or _bot_task.done():
        return {"ok": False, "message": "Bot is not running"}
    _bot_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(_bot_task), timeout=5.0)
    except Exception:
        pass
    return {"ok": True, "message": "Bot stopped"}


async def _broadcast_loop():
    while True:
        try:
            if manager.active:
                payload = _build_state_payload()
                payload["bot_status"] = _bot_status.copy()
                await manager.broadcast(payload)
        except Exception as exc:
            log.warning(f"Broadcast error: {exc}")
        await asyncio.sleep(1)


# -- Lifespan -----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load all credentials from Redis into env on startup
    try:
        from api.token_manager import get_stored_token
        t = get_stored_token()
        if t:
            os.environ["UPSTOX_ACCESS_TOKEN"] = t
            log.info("Loaded Upstox token from Redis on startup")
    except Exception:
        pass
    try:
        from infra.redis_bus import get_data as _gd
        k = _gd("upstox_api_key")
        s = _gd("upstox_api_secret")
        if k: os.environ["UPSTOX_API_KEY"]    = k
        if s: os.environ["UPSTOX_API_SECRET"] = s
    except Exception:
        pass
    if os.getenv("BOT_ENABLED", "true").lower() == "true":
        await _start_bot()
    asyncio.create_task(_broadcast_loop())
    yield


# ── App + CORS ────────────────────────────────────────────────────────────────
app = FastAPI(title="Trading Bot API", version="2.0.0", lifespan=lifespan)

RENDER_URL = "https://trading-bot-av9x.onrender.com"
VERCEL_URL = os.getenv("FRONTEND_URL", "https://trading-bot-seven-tawny.vercel.app").strip().rstrip("/")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS — open /auth in browser to refresh token daily
# ═════════════════════════════════════════════════════════════════════════════

_AUTH_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Upstox Token Refresh</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0f1117;color:#e2e8f0;font-family:system-ui,sans-serif;
          display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
    .card{{background:#1a1f2e;border:1px solid #2d3748;border-radius:16px;
           padding:40px;max-width:480px;width:100%;text-align:center}}
    h1{{font-size:1.5rem;margin-bottom:8px;color:#63b3ed}}
    .status{{font-size:0.9rem;padding:10px 16px;border-radius:8px;margin:16px 0}}
    .ok{{background:#1c4532;color:#68d391;border:1px solid #276749}}
    .expired{{background:#742a2a;color:#fc8181;border:1px solid #9b2c2c}}
    .btn{{display:inline-block;background:#3182ce;color:white;padding:14px 32px;
          border-radius:10px;text-decoration:none;font-weight:700;font-size:1rem;
          margin-top:20px;transition:background 0.2s}}
    .btn:hover{{background:#2b6cb0}}
    .note{{font-size:0.8rem;color:#718096;margin-top:16px;line-height:1.5}}
  </style>
</head>
<body>
<div class="card">
  <h1>📈 Upstox Token Refresh</h1>
  <p style="color:#a0aec0;margin-top:4px">Trading Bot — Daily Auth</p>
  <div class="status {status_class}">{status_msg}</div>
  <a href="{login_url}" class="btn">🔐 Login with Upstox</a>
  <p class="note">
    Click the button → Login with your Upstox credentials<br>
    Token saves automatically — takes about 10 seconds<br>
    Do this once each morning before 9:15 AM IST
  </p>
</div>
</body>
</html>"""

_SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Token Saved</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0f1117;color:#e2e8f0;font-family:system-ui,sans-serif;
          display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
    .card{{background:#1a1f2e;border:1px solid #2d3748;border-radius:16px;
           padding:40px;max-width:480px;width:100%;text-align:center}}
    h1{{font-size:1.5rem;color:#68d391;margin-bottom:12px}}
    .tick{{font-size:4rem;margin-bottom:16px}}
    p{{color:#a0aec0;line-height:1.6;margin-top:8px}}
    .token{{font-family:monospace;font-size:0.8rem;color:#63b3ed;
            background:#1e2535;padding:8px 12px;border-radius:6px;
            margin:12px 0;word-break:break-all}}
    .note{{font-size:0.8rem;color:#718096;margin-top:16px}}
  </style>
</head>
<body>
<div class="card">
  <div class="tick">✅</div>
  <h1>Token Saved!</h1>
  <p>Your Upstox access token has been saved to Redis.</p>
  <div class="token">{token_preview}...</div>
  <p>The bot will pick up the new token within 60 seconds.</p>
  <p class="note">You can close this tab. See you tomorrow! 🙏</p>
</div>
</body>
</html>"""

_ERROR_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Auth Error</title>
  <style>
    body{{background:#0f1117;color:#e2e8f0;font-family:system-ui,sans-serif;
          display:flex;align-items:center;justify-content:center;min-height:100vh}}
    .card{{background:#1a1f2e;border:1px solid #742a2a;border-radius:16px;
           padding:40px;max-width:480px;text-align:center}}
    h1{{color:#fc8181}} pre{{background:#141824;padding:12px;border-radius:8px;
    font-size:0.8rem;text-align:left;overflow-x:auto;margin-top:12px}}
    a{{color:#63b3ed}}
  </style>
</head>
<body>
<div class="card">
  <h1>❌ Auth Error</h1>
  <pre>{error}</pre>
  <p style="margin-top:16px"><a href="/auth">← Try again</a></p>
</div>
</body>
</html>"""


@app.get("/auth", response_class=HTMLResponse)
def auth_page():
    """
    Open this in your browser each morning to refresh the Upstox token.
    URL: https://trading-bot-av9x.onrender.com/auth
    """
    from api.token_manager import get_stored_token, check_token_valid, get_login_url
    token = get_stored_token()
    valid = check_token_valid(token) if token else False
    if valid:
        status_class = "ok"
        status_msg   = "✅ Token is valid — bot is running"
    else:
        status_class = "expired"
        status_msg   = "⚠️ Token expired — please login below"
    return _AUTH_HTML.format(
        status_class=status_class,
        status_msg=status_msg,
        login_url=get_login_url(),
    )


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(code: str = "", error: str = ""):
    """
    Upstox redirects here after login with ?code=...
    Exchanges code for token and saves to Redis.
    """
    from api.token_manager import exchange_code, save_token
    if error:
        return HTMLResponse(_ERROR_HTML.format(error=f"Upstox error: {error}"), status_code=400)
    if not code:
        return HTMLResponse(_ERROR_HTML.format(error="No auth code received from Upstox"), status_code=400)
    try:
        token = exchange_code(code)
        save_token(token)
        log.info(f"New Upstox token saved via /auth/callback ({token[:20]}...)")
        return _SUCCESS_HTML.format(token_preview=token[:40])
    except Exception as exc:
        log.error(f"Auth callback error: {exc}")
        return HTMLResponse(_ERROR_HTML.format(error=str(exc)), status_code=500)


@app.get("/auth/status")
def auth_status():
    """Check if the current token is valid."""
    from api.token_manager import get_stored_token, check_token_valid
    token = get_stored_token()
    valid = check_token_valid(token) if token else False
    return {
        "token_valid":   valid,
        "token_present": bool(token),
        "token_preview": token[:20] + "..." if token else None,
        "refresh_url":   "/auth",
    }


# ═════════════════════════════════════════════════════════════════════════════
# STANDARD ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

# --- Settings endpoints (credentials management) ----------------------------
class CredentialsIn(BaseModel):
    api_key:      str = ""
    api_secret:   str = ""
    access_token: str = ""


@app.get("/settings")
def get_settings():
    """Return current credentials (secrets masked)."""
    from api.settings import load_credentials, check_token
    creds = load_credentials()
    token = creds.get("access_token", "")
    status = check_token(token) if token else {"valid": False, "reason": "No token"}
    return {
        "api_key":          creds.get("api_key", ""),
        "api_secret_set":   bool(creds.get("api_secret", "")),
        "api_secret_hint":  creds.get("api_secret", "")[:4] + "****" if creds.get("api_secret") else "",
        "token_valid":      status.get("valid", False),
        "token_hint":       token[:20] + "..." if token else "",
        "user_name":        status.get("name", ""),
        "login_url":        "/auth",
    }


@app.post("/settings")
def save_settings(creds: CredentialsIn):
    """Save credentials to Redis. Called from the website settings modal."""
    from api.settings import save_credentials, check_token
    result = save_credentials(
        api_key=creds.api_key.strip(),
        api_secret=creds.api_secret.strip(),
        access_token=creds.access_token.strip(),
    )
    # If token provided, validate it immediately
    token_status = {}
    if creds.access_token.strip():
        token_status = check_token(creds.access_token.strip())
    return {**result, "token_status": token_status}


@app.get("/settings/token-status")
def token_status():
    """Quick check: is the current token valid?"""
    from api.settings import load_credentials, check_token
    creds = load_credentials()
    token = creds.get("access_token", "")
    status = check_token(token) if token else {"valid": False, "reason": "No token"}
    return status



@app.get("/bot/status")
def bot_status_endpoint():
    """Current bot running state."""
    return {
        **_bot_status,
        "task_done": _bot_task.done() if _bot_task else True,
    }


@app.post("/bot/start")
async def bot_start():
    """Start the trading bot."""
    result = await _start_bot()
    return result


@app.post("/bot/stop")
async def bot_stop():
    """Stop the trading bot gracefully."""
    result = await _stop_bot()
    return result


@app.post("/bot/restart")
async def bot_restart():
    """Stop then start the bot (picks up new credentials)."""
    await _stop_bot()
    await asyncio.sleep(1)
    result = await _start_bot()
    return {**result, "message": "Bot restarted"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    """UptimeRobot keep-alive — prevents Render free tier sleeping."""
    return {"pong": True, "time": _dt.datetime.utcnow().isoformat() + "Z"}


@app.get("/state")
def get_state():
    return _build_state_payload()


@app.get("/state/{index}")
def get_index_state(index: str):
    idx        = index.upper()
    pnl_raw    = get_data(f"pnl_{idx}")
    spot_raw   = get_data(f"spot_{idx}")
    regime_raw = get_data(f"regime_{idx}")
    return {
        "index":  idx,
        "pnl":    json.loads(pnl_raw) if pnl_raw else None,
        "spot":   float(spot_raw)     if spot_raw else None,
        "regime": regime_raw,
    }


@app.get("/history")
def get_history():
    try:
        from execution.paper_engine import load_trade_history
        return {"trades": load_trade_history()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/all_state")
def get_all_state():
    return get_all_data()


class CloseCmd(BaseModel):
    index: str
    action: str = "close_all"
    position_index: int = -1


@app.post("/close")
def close_position(cmd: CloseCmd):
    import tempfile
    STATE_FILE = os.path.join(tempfile.gettempdir(), "trading_bot_state.json")
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        cmds = data.get("close_commands", [])
        if isinstance(cmds, str):
            cmds = json.loads(cmds)
        cmds.append({
            "index":          cmd.index.upper(),
            "action":         cmd.action,
            "position_index": cmd.position_index,
        })
        data["close_commands"] = cmds
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return {"status": "ok", "message": f"Close command queued for {cmd.index}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    log.info(f"WS connected. Total: {len(manager.active)}")
    try:
        await websocket.send_json(_build_state_payload())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        log.info(f"WS disconnected. Total: {len(manager.active)}")
