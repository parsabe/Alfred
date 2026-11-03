"""`POST /v1/ask`: one question, one JSON answer. No streaming.

Made for clients that can only do a simple HTTP request: Apple Shortcuts (iPhone, Apple Watch,
"Hey Siri, ask Alfred"), Android HTTP-shortcut/Tasker apps, Wear OS tiles, home-automation hooks.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import get_agent, get_store
from app.core.agent import Agent
from app.memory.short_term import ConversationStore
from app.security.auth import OwnerDep
from app.voice.base import VoiceError
from app.voice.chunker import clean_for_speech

router = APIRouter(prefix="/v1", tags=["ask"], dependencies=[OwnerDep])

LATEST_WINDOW = timedelta(minutes=30)


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default="latest",
        description='A conversation id, "latest" (continue the conversation used in the last 30 '
        "minutes, else start a new one), or null for a fresh conversation.",
    )
    voice: bool = Field(
        default=False,
        description="Answer in speakable sentences and include Alfred's voice as base64 audio.",
    )


class AskResponse(BaseModel):
    reply: str
    conversation_id: str | None
    error: str | None = None
    audio_b64: str | None = None
    audio_media_type: str | None = None


@router.post("/ask", response_model=AskResponse, summary="Ask once, get the whole answer as JSON")
async def ask(
    body: AskRequest,
    request: Request,
    agent: Annotated[Agent, Depends(get_agent)],
    store: Annotated[ConversationStore, Depends(get_store)],
) -> AskResponse:
    conversation_id = body.conversation_id
    if conversation_id == "latest":
        rows, _ = await store.list_page(limit=1)
        recent = rows[0] if rows else None
        updated = recent.updated_at if recent else None
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        conversation_id = (
            recent.id
            if recent and updated and datetime.now(UTC) - updated < LATEST_WINDOW
            else None
        )
    elif conversation_id and await store.get(conversation_id) is None:
        conversation_id = None

    parts: list[str] = []
    error: str | None = None
    final_id = conversation_id
    async for event in agent.run_turn(
        body.message, conversation_id, "voice" if body.voice else "text"
    ):
        final_id = event.conversation_id or final_id
        if event.type == "token":
            parts.append(event.data["text"])
        elif event.type == "error":
            error = event.data["message"]
    reply = "".join(parts).strip() or (error or "")

    audio_b64 = media_type = None
    tts = request.app.state.tts
    if body.voice and tts is not None and reply:
        try:
            speech = await tts.synthesize(clean_for_speech(reply)[:2000])
            audio_b64, media_type = base64.b64encode(speech.audio).decode(), speech.media_type
        except VoiceError:
            pass
    return AskResponse(
        reply=reply,
        conversation_id=final_id,
        error=error,
        audio_b64=audio_b64,
        audio_media_type=media_type,
    )
