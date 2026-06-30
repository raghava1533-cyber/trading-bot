"""
api/server.py  -  FastAPI backend for Render FREE tier deployment
Exposes bot state via REST + WebSocket. Runs bot as background task.
Keep-alive: UptimeRobot pings /ping every 5 min to prevent free tier sleep.
"""
import asyncio, datetime as _dt, json, logging, os, sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
async def _run_bot():
    try:
        from main_async import main as bot_main
        log.info("Starting trading bot background task...")
        await bot_main()
    except Exception as exc:
        log.error(f"Bot crashed: {exc}", exc_info=True)


async def _broadcast_loop():
    while True:
        try:
            if manager.active:
                await manager.broadcast(_build_state_payload())
        except Exception as exc:
            log.warning(f"Broadcast error: {exc}")
        await asyncio.sleep(1)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("BOT_ENABLED", "true").lower() == "true":
        asyncio.create_task(_run_bot())
    asyncio.create_task(_broadcast_loop())
    yield


# ── App + CORS ────────────────────────────────────────────────────────────────
app = FastAPI(title="Trading Bot API", version="2.0.0", lifespan=lifespan)

FRONTEND_URL = os.getenv("FRONTEND_URL", "*")
# Allow configured frontend + localhost for dev
_origins = ["*"] if FRONTEND_URL == "*" else [
    FRONTEND_URL,
    "http://localhost:3000",
    "http://localhost:8501",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Render health check endpoint."""
    return {"status": "ok"}


@app.get("/ping")
def ping():
    """
    Keep-alive endpoint for UptimeRobot (prevents Render free tier sleeping).
    Setup: https://uptimerobot.com → New Monitor → HTTP(s)
    URL:   https://trading-bot-api.onrender.com/ping
    Interval: every 5 minutes (free UptimeRobot account)
    """
    return {"pong": True, "time": _dt.datetime.utcnow().isoformat() + "Z"}


@app.get("/state")
def get_state():
    """Full bot state for all active indices."""
    return _build_state_payload()


@app.get("/state/{index}")
def get_index_state(index: str):
    """Bot state for a single index."""
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
    """Full trade history."""
    try:
        from execution.paper_engine import load_trade_history
        return {"trades": load_trade_history()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/all_state")
def get_all_state():
    """Return every key in the state store (debug)."""
    return get_all_data()


# ── Close command ─────────────────────────────────────────────────────────────
class CloseCmd(BaseModel):
    index: str
    action: str = "close_all"
    position_index: int = -1


@app.post("/close")
def close_position(cmd: CloseCmd):
    """Queue a close command for the bot (picked up next cycle)."""
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


# ── WebSocket ─────────────────────────────────────────────────────────────────
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
