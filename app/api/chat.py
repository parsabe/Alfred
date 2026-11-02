from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_agent, get_store
from app.core.agent import Agent
from app.memory.short_term import ConversationStore
from app.security.auth import OwnerDep

router = APIRouter(prefix="/v1", tags=["chat"], dependencies=[OwnerDep])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    output_mode: Literal["text", "voice"] = "text"
    attachments: list[dict[str, Any]] = Field(
        default_factory=list, description="Reserved; not supported yet (must be empty)."
    )


@router.post(
    "/chat",
    summary="Send a message; the response is a Server-Sent Events stream",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Event stream"}},
)
async def chat(
    body: ChatRequest,
    agent: Annotated[Agent, Depends(get_agent)],
    store: Annotated[ConversationStore, Depends(get_store)],
) -> EventSourceResponse:
    if body.attachments:
        raise HTTPException(422, "attachments are not supported yet")
    if body.conversation_id is not None and await store.get(body.conversation_id) is None:
        raise HTTPException(404, "conversation not found")

    async def stream() -> AsyncIterator[dict[str, str]]:
        async for event in agent.run_turn(body.message, body.conversation_id, body.output_mode):
            yield event.sse()

    return EventSourceResponse(stream(), ping=15)
