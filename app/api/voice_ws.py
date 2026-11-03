"""WebSocket transport for hands-free voice: `WS /v1/voice`.

Client → server
  text  {"type": "hello", "mode": "wake"|"ptt"|"always", "conversation_id": "...",
         "sample_rate": 16000, "barge_in": true|false (optional)}
        (must be the first message)
  bytes raw microphone audio: 16-bit little-endian mono PCM at 16 kHz, any chunk size
  text  {"type": "wake"}                       tap-to-talk / button
  text  {"type": "text", "message": "..."}     typed message
  text  {"type": "playback", "state": "ended"} finished playing Alfred's audio for the turn
  text  {"type": "interrupt"} | {"type": "stop"} | {"type": "sleep"} | {"type": "mode", "mode": ...}

Server → client: JSON events (see docs/API.md): ready, state, user_speech, transcript, token,
audio, duck, unduck, interrupt, error, done.

Auth: `Authorization: Bearer <token>` header, or (browsers) the subprotocols
`["bearer", "<token>"]`. Rejected connections are closed with code 4401 / 4403.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.voice_session import Mode, VoiceSession
from app.security.auth import _client_allowed, token_matches, websocket_token

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/v1/voice")
async def voice(ws: WebSocket) -> None:
    app = ws.app
    settings = app.state.settings
    offered = [p.strip().lower() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
    await ws.accept(subprotocol="bearer" if "bearer" in offered else None)
    if not _client_allowed(ws.client.host if ws.client else None, settings.ip_allowlist):
        await ws.close(code=4403, reason="forbidden")
        return
    if not token_matches(websocket_token(ws), settings):
        await ws.close(code=4401, reason="invalid or missing token")
        return

    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=15))
        assert isinstance(hello, dict) and hello.get("type") == "hello"
    except (TimeoutError, AssertionError, json.JSONDecodeError, WebSocketDisconnect, KeyError):
        with contextlib.suppress(Exception):
            await ws.close(code=4400, reason="expected a hello message first")
        return
    if int(hello.get("sample_rate", 16000)) != 16000:
        await ws.close(code=4400, reason="audio must be 16 kHz mono 16-bit PCM")
        return

    conversation_id = hello.get("conversation_id") or None
    if conversation_id and await app.state.store.get(conversation_id) is None:
        conversation_id = None
    requested = hello.get("mode")
    mode: Mode = requested if requested in ("wake", "ptt", "always") else "wake"
    session = VoiceSession(
        agent=app.state.agent,
        settings=settings,
        stt=app.state.stt,
        tts=app.state.tts,
        mode=mode,
        conversation_id=conversation_id,
        barge_in=hello["barge_in"] if isinstance(hello.get("barge_in"), bool) else None,
        speech_test=getattr(app.state, "speech_test", None),
    )
    session.ready()

    async def sender() -> None:
        while True:
            event = await session.events.get()
            await ws.send_text(event.model_dump_json())

    send_task = asyncio.create_task(sender())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.feed_audio(message["bytes"])
            elif message.get("text"):
                try:
                    control = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(control, dict):
                    await session.handle_control(control)
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await send_task
