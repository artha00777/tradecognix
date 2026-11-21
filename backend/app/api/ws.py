"""WebSocket: live session status, orders, trades and events for the signed-in user."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from ..db import get_engine
from ..engine.live import engine
from .deps import user_from_token

router = APIRouter()


@router.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    token = ws.query_params.get("token")
    with Session(get_engine()) as db:
        user = user_from_token(token, db)
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    uid, is_admin = user.id, user.role == "admin"

    def listener(user_id: int, payload: dict):
        if user_id == uid or is_admin:
            loop.call_soon_threadsafe(lambda: queue.full() or queue.put_nowait(payload))

    engine.listeners.append(listener)
    try:
        await ws.send_json({"type": "hello", "sessions": engine.status(None if is_admin else uid)})
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=5)
                await ws.send_text(json.dumps(msg, default=str))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "snapshot", "sessions": engine.status(None if is_admin else uid)}, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        if listener in engine.listeners:
            engine.listeners.remove(listener)
