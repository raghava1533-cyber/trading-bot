"""
api/server.py
─────────────
FastAPI server that:
  1. Exposes live bot state (PnL, spot, regime, positions) via REST + WebSocket
  2. Runs the trading bot as a background asyncio task
  3. Reads/writes state through Redis (or in-memory fallback)

Deploy this on Render as a Web Service.
Start command:  uvicorn api.server:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import json
import os
import sys
import logging
from contextlib import asynccontextmanager

# ── make sure `core/` is importable ──────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from infra.redis_bus import get_data, set_data

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ── WebSocket connection manager ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = ConnectionManager()


# ── background bot task ───────────────────────────────────────────────────────
async def _run_bot():
    """
    Imports and runs the trading bot's main() coroutine.
    Isolated so a crash here doesn't kill the API server.
    """
    try:
        from main_async import main as bot_main
        log.info("🤖 Starting trading bot background task...")
        await bot_main()
    except Exception as e:
        log.error(f"Bot crashed: {e}", exc_info=True)


# ── broadcast loop: push Redis state to all WS clients every second ───────────
async def _broadcast_loop():
    while True:
        try:
            payload = _build_state_payload()
            if manager.active:
                await manager.broadcast(payload)
        except Exception as e:
            log.warning(f"Broadcast error: {e}")
        await asyncio.sleep(1)


def _build_state_payload() -> dict:
    """Read latest state from Redis and return as a dict."""
    indices = ["NIFTY", "BANKNIFTY", "SENSEX"]
    state = {}
    for idx in indices:
        pnl_raw    = get_data(f"pnl_{idx}")
        spot_raw   = get_data(f"spot_{idx}")
        regime_raw = get_data(f"regime_{idx}")

        state[idx] = {
            "pnl":    json.loads(pnl_raw)  if pnl_raw    else None,
            "spot":   float(spot_raw)       if spot_raw   else None,
            "regime": regime_raw            if regime_raw else None,
        }
    return state


# ── lifespan: start background tasks on startup ───────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start bot only if BOT_ENABLED env var is set (so you can run API-only mode)
    if os.getenv("BOT_ENABLED", "true").lower() == "true":
        asyncio.create_task(_run_bot())
    asyncio.create_task(_broadcast_loop())
    yield


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Trading Bot API", lifespan=lifespan)

# Allow Vercel frontend origin — set FRONTEND_URL in Render env vars
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
    """Return current bot state for all indices."""
    return _build_state_payload()


@app.get("/state/{index}")
def get_index_state(index: str):
    """Return current bot state for a single index (NIFTY / BANKNIFTY / SENSEX)."""
    idx = index.upper()
    pnl_raw    = get_data(f"pnl_{idx}")
    spot_raw   = get_data(f"spot_{idx}")
    regime_raw = get_data(f"regime_{idx}")

    return {
        "index":  idx,
        "pnl":    json.loads(pnl_raw)  if pnl_raw    else None,
        "spot":   float(spot_raw)       if spot_raw   else None,
        "regime": regime_raw            if regime_raw else None,
    }


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    log.info(f"WS client connected. Total: {len(manager.active)}")
    try:
        # Send current state immediately on connect
        await websocket.send_json(_build_state_payload())
        # Keep connection alive; broadcast loop handles updates
        while True:
            await websocket.receive_text()   # ping/pong or client messages
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        log.info(f"WS client disconnected. Total: {len(manager.active)}")
