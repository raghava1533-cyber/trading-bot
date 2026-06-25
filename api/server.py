"""
api/server.py
─────────────
FastAPI server that:
  1. Exposes live bot state (PnL, spot, regime, positions) via REST + WebSocket
  2. Runs the trading bot as a background asyncio task
  3. Reads/writes state through Redis (or in-memory / STATE_FILE fallback)

Deploy on Render as a Web Service.
Start command:  uvicorn api.server:app --host 0.0.0.0 --port $PORT
"""
import asyncio, json, logging, os, sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from infra.redis_bus import get_data, get_all_data, set_data

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s",
                    level=logging.INFO)
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
    """Read latest state from Redis / STATE_FILE for all active indices."""
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
            "regime": regime_raw          if regime_raw else None,
        }

    # Include full positions blob if available
    all_pos_raw = get_data("all_positions")
    if all_pos_raw:
        try:
            state["all_positions"] = json.loads(all_pos_raw)
        except Exception:
            state["all_positions"] = {}

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


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Trading Bot API", version="1.0.0", lifespan=lifespan)

FRONTEND_URL = os.getenv("FRONTEND_URL", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL] if FRONTEND_URL != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


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
        "regime": regime_raw          if regime_raw else None,
    }


@app.get("/all_state")
def get_all_state():
    """Return every key in the state store (debug endpoint)."""
    return get_all_data()


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
