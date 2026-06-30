import asyncio, datetime as _dt, json, logging, os, sys
from contextlib import asynccontextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from infra.redis_bus import get_data, get_all_data, set_data

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active = []
    async def connect(self, ws):
        await ws.accept(); self.active.append(ws)
    def disconnect(self, ws):
        if ws in self.active: self.active.remove(ws)
    async def broadcast(self, data):
        dead = []
        for ws in self.active:
            try: await ws.send_json(data)
            except Exception: dead.append(ws)
        for ws in dead: self.disconnect(ws)

manager = ConnectionManager()


def _build_state_payload():
    try:
        from config import SETTINGS
        indices = list(SETTINGS.active_indices)
    except Exception:
        indices = ["NIFTY", "BANKNIFTY", "SENSEX"]
    state = {"indices": {}}
    for idx in indices:
        pnl_raw    = get_data("pnl_"    + idx)
        spot_raw   = get_data("spot_"   + idx)
        regime_raw = get_data("regime_" + idx)
        state["indices"][idx] = {
            "pnl":    json.loads(pnl_raw) if pnl_raw else None,
            "spot":   float(spot_raw)     if spot_raw else None,
            "regime": regime_raw,
        }
    all_pos_raw = get_data("all_positions")
    if all_pos_raw:
        try: state["all_positions"] = json.loads(all_pos_raw)
        except Exception: state["all_positions"] = {}
    last_update = get_data("last_update")
    if last_update: state["last_update"] = last_update
    try:
        from execution.paper_engine import load_trade_history
        state["trade_history"] = load_trade_history()
    except Exception:
        state["trade_history"] = []
    return state


async def _run_bot():
    try:
        from main_async import main as bot_main
        log.info("Starting trading bot background task...")
        await bot_main()
    except Exception as exc:
        log.error("Bot crashed: " + str(exc), exc_info=True)


async def _broadcast_loop():
    while True:
        try:
            if manager.active:
                await manager.broadcast(_build_state_payload())
        except Exception as exc:
            log.warning("Broadcast error: " + str(exc))
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    try:
        from api.token_manager import get_stored_token
        t = get_stored_token()
        if t: os.environ["UPSTOX_ACCESS_TOKEN"] = t; log.info("Token loaded from Redis on startup")
    except Exception:
        pass
    if os.getenv("BOT_ENABLED", "true").lower() == "true":
        asyncio.create_task(_run_bot())
    asyncio.create_task(_broadcast_loop())
    yield


app = FastAPI(title="Trading Bot API", version="2.0.0", lifespan=lifespan)
FRONTEND_URL = os.getenv("FRONTEND_URL", "*")
_origins = ["*"] if FRONTEND_URL == "*" else [FRONTEND_URL, "http://localhost:3000", "http://localhost:8501"]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# --- Auth HTML pages (plain strings, no f-string, no unicode) ----------------
def _auth_page_html(status_ok, login_url):
    status_class = "ok" if status_ok else "expired"
    status_msg   = "Token is valid - bot is running" if status_ok else "Token expired - please login below"
    return (
        "<!DOCTYPE html><html><head><title>Upstox Token Refresh</title>"
        "<meta name=viewport content=width=device-width,initial-scale=1>"
        "<style>"
        "body{background:#0f1117;color:#e2e8f0;font-family:system-ui,sans-serif;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}"
        ".card{background:#1a1f2e;border:1px solid #2d3748;border-radius:16px;"
        "padding:40px;max-width:480px;width:100%;text-align:center}"
        "h1{font-size:1.5rem;margin-bottom:8px;color:#63b3ed}"
        ".ok{background:#1c4532;color:#68d391;border:1px solid #276749;padding:10px 16px;border-radius:8px;margin:16px 0}"
        ".expired{background:#742a2a;color:#fc8181;border:1px solid #9b2c2c;padding:10px 16px;border-radius:8px;margin:16px 0}"
        ".btn{display:inline-block;background:#3182ce;color:white;padding:14px 32px;"
        "border-radius:10px;text-decoration:none;font-weight:700;font-size:1rem;margin-top:20px}"
        ".note{font-size:0.8rem;color:#718096;margin-top:16px;line-height:1.5}"
        "</style></head><body><div class=card>"
        "<h1>Upstox Token Refresh</h1>"
        "<p style=color:#a0aec0;margin-top:4px>Trading Bot - Daily Auth</p>"
        "<div class=" + status_class + ">" + status_msg + "</div>"
        "<a href=" + login_url + " class=btn>Login with Upstox</a>"
        "<p class=note>Click the button, login with Upstox credentials.<br>"
        "Token saves automatically. Takes about 10 seconds.<br>"
        "Do this once each morning before 9:15 AM IST.</p>"
        "</div></body></html>"
    )

def _success_html(token_preview):
    return (
        "<!DOCTYPE html><html><head><title>Token Saved</title>"
        "<style>body{background:#0f1117;color:#e2e8f0;font-family:system-ui,sans-serif;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh}"
        ".card{background:#1a1f2e;border:1px solid #2d3748;border-radius:16px;"
        "padding:40px;max-width:480px;text-align:center}"
        "h1{color:#68d391}.token{font-family:monospace;font-size:0.8rem;color:#63b3ed;"
        "background:#1e2535;padding:8px 12px;border-radius:6px;margin:12px 0;word-break:break-all}"
        ".note{font-size:0.8rem;color:#718096;margin-top:16px}"
        "</style></head><body><div class=card>"
        "<div style=font-size:4rem>OK</div>"
        "<h1>Token Saved!</h1>"
        "<p>Your Upstox access token has been saved to Redis.</p>"
        "<div class=token>" + token_preview + "...</div>"
        "<p>The bot will pick up the new token within 60 seconds.</p>"
        "<p class=note>You can close this tab.</p>"
        "</div></body></html>"
    )

def _error_html(error):
    return (
        "<!DOCTYPE html><html><head><title>Auth Error</title>"
        "<style>body{background:#0f1117;color:#e2e8f0;font-family:system-ui,sans-serif;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh}"
        ".card{background:#1a1f2e;border:1px solid #742a2a;border-radius:16px;"
        "padding:40px;max-width:480px;text-align:center}"
        "h1{color:#fc8181}pre{background:#141824;padding:12px;border-radius:8px;"
        "font-size:0.8rem;text-align:left;overflow-x:auto;margin-top:12px}"
        "a{color:#63b3ed}"
        "</style></head><body><div class=card>"
        "<h1>Auth Error</h1>"
        "<pre>" + str(error) + "</pre>"
        "<p style=margin-top:16px><a href=/auth>Try again</a></p>"
        "</div></body></html>"
    )


# --- Auth endpoints ----------------------------------------------------------
@app.get("/auth", response_class=HTMLResponse)
def auth_page():
    from api.token_manager import get_stored_token, check_token_valid, get_login_url
    token = get_stored_token()
    valid = check_token_valid(token) if token else False
    return _auth_page_html(valid, get_login_url())


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(code: str = "", error: str = ""):
    from api.token_manager import exchange_code, save_token
    if error:
        return HTMLResponse(_error_html("Upstox error: " + error), status_code=400)
    if not code:
        return HTMLResponse(_error_html("No auth code received from Upstox"), status_code=400)
    try:
        token = exchange_code(code)
        save_token(token)
        log.info("New Upstox token saved via /auth/callback")
        return _success_html(token[:40])
    except Exception as exc:
        log.error("Auth callback error: " + str(exc))
        return HTMLResponse(_error_html(str(exc)), status_code=500)


@app.get("/auth/status")
def auth_status():
    from api.token_manager import get_stored_token, check_token_valid
    token = get_stored_token()
    valid = check_token_valid(token) if token else False
    return {"token_valid": valid, "token_present": bool(token),
            "token_preview": token[:20] + "..." if token else None,
            "refresh_url": "/auth"}


# --- Standard endpoints ------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"pong": True, "time": _dt.datetime.utcnow().isoformat() + "Z"}


@app.get("/state")
def get_state():
    return _build_state_payload()


@app.get("/state/{index}")
def get_index_state(index: str):
    idx = index.upper()
    pnl_raw    = get_data("pnl_"    + idx)
    spot_raw   = get_data("spot_"   + idx)
    regime_raw = get_data("regime_" + idx)
    return {"index": idx,
            "pnl":    json.loads(pnl_raw) if pnl_raw else None,
            "spot":   float(spot_raw)     if spot_raw else None,
            "regime": regime_raw}


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
        if isinstance(cmds, str): cmds = json.loads(cmds)
        cmds.append({"index": cmd.index.upper(), "action": cmd.action,
                     "position_index": cmd.position_index})
        data["close_commands"] = cmds
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return {"status": "ok", "message": "Close command queued for " + cmd.index}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    log.info("WS connected. Total: " + str(len(manager.active)))
    try:
        await websocket.send_json(_build_state_payload())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        log.info("WS disconnected. Total: " + str(len(manager.active)))
