"""Realtime voice transport (Phase 2B): WebSocket `/api/v1/voice/stream`.

Protocol: typed JSON frames defined in `nodo/voice/protocol.py`; binary frames count as `audio.chunk`.
Auth mirrors the REST surface: if `NODO_API_TOKEN` is set, the client passes `?token=` (browsers can't set
headers on WebSocket) or an Authorization header.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from nodo.app import Container
from nodo.db.session import session_scope
from nodo.voice import protocol
from nodo.voice.runtime import VoiceRuntime

log = logging.getLogger("nodo.voice.ws")

router = APIRouter()


@router.websocket("/api/v1/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    c: Container = ws.app.state.container
    token = ws.app.state.settings.api_token
    if token:
        provided = ws.query_params.get("token") or (ws.headers.get("authorization") or "").removeprefix("Bearer ")
        if provided != token:
            await ws.close(code=4401)
            return
    await ws.accept()

    send_q: asyncio.Queue[dict | None] = asyncio.Queue()

    async def sender() -> None:
        while (m := await send_q.get()) is not None:
            await ws.send_text(json.dumps(m, ensure_ascii=False, default=str))

    async def emit(ev: dict) -> None:
        send_q.put_nowait(ev)

    send_task = asyncio.create_task(sender())
    runtime: VoiceRuntime | None = None
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if runtime:
                    await runtime.handle(msg["bytes"])
                continue
            raw = msg.get("text")
            if raw is None:
                continue
            try:
                inbound = protocol.INBOUND.validate_json(raw)
            except ValidationError as e:
                await emit(protocol.ev("error", session_id=runtime.vs.id if runtime else "-",
                                       where="protocol", message=f"invalid message: {e.errors()[0]['msg']}"))
                continue
            if isinstance(inbound, protocol.SessionStart) and runtime is None:
                vs = c.voice_sessions.create(inbound.conversation_id, inbound.language)
                runtime = VoiceRuntime(vs, c, emit, session_scope)
            if runtime is None:
                await emit(protocol.ev("error", session_id="-", where="protocol",
                                       message="send session.start first"))
                continue
            await runtime.handle(inbound)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("voice stream crashed")
    finally:
        if runtime:
            await runtime.close()
        send_q.put_nowait(None)
        with suppress(Exception):
            await asyncio.wait_for(send_task, timeout=2)
        send_task.cancel()
