"""WebSocket: live session status, orders, trades and events for the signed-in user.

Auth (either):
- subprotocol header `Sec-WebSocket-Protocol: tc-auth, <jwt>` (preferred: tokens stay out of URLs/logs), or
- `?token=<jwt>` query parameter (legacy; used by the current web client).
The token is re-validated periodically, so an expired token or a disabled account is disconnected.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from ..db import get_engine
from ..engine.live import engine
from .deps import user_from_token

router = APIRouter()
AUTH_SUBPROTOCOL = "tc-auth"
REVALIDATE_SECONDS = 60
MAX_CONNECTIONS_PER_USER = 5
_connections: dict[int, int] = defaultdict(int)


def _token_from(ws: WebSocket) -> tuple[str | None, str | None]:
    """(token, subprotocol to echo back)."""
    protos = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    if len(protos) >= 2 and protos[0] == AUTH_SUBPROTOCOL:
        return protos[1], AUTH_SUBPROTOCOL
    return ws.query_params.get("token"), None


def _lookup(token: str | None):
    with Session(get_engine()) as db:
        return user_from_token(token, db)


@router.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    token, subprotocol = _token_from(ws)
    user = _lookup(token)
    if not user:
        await ws.close(code=4401)
        return
    uid, is_admin = user.id, user.role == "admin"
    if _connections[uid] >= MAX_CONNECTIONS_PER_USER:
        await ws.close(code=4429)
        return
    await ws.accept(subprotocol=subprotocol)
    _connections[uid] += 1
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    def _put(payload: dict) -> None:
        if not queue.full():
            queue.put_nowait(payload)

    def listener(user_id: int, payload: dict):
        if user_id == uid or is_admin:
            loop.call_soon_threadsafe(_put, payload)

    engine.listeners.append(listener)
    last_check = time.monotonic()
    try:
        await ws.send_json({"type": "hello", "sessions": engine.status(None if is_admin else uid)})
        while True:
            if time.monotonic() - last_check > REVALIDATE_SECONDS:
                last_check = time.monotonic()
                if not _lookup(token):  # expired token or disabled account
                    await ws.close(code=4401)
                    break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=5)
                await ws.send_text(json.dumps(msg, default=str))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "snapshot", "sessions": engine.status(None if is_admin else uid)}, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _connections[uid] = max(0, _connections[uid] - 1)
        if listener in engine.listeners:
            engine.listeners.remove(listener)
