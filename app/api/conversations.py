from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.deps import get_store
from app.memory.short_term import ConversationStore
from app.security.auth import OwnerDep

router = APIRouter(prefix="/v1/conversations", tags=["conversations"], dependencies=[OwnerDep])


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ConversationPage(BaseModel):
    items: list[ConversationSummary]
    total: int
    next_before: datetime | None


class MessageOut(BaseModel):
    id: str
    seq: int
    role: str
    text: str
    content: list[dict[str, Any]]
    usage: dict[str, Any] | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]


@router.get("", response_model=ConversationPage, summary="List conversations, newest first")
async def list_conversations(
    store: Annotated[ConversationStore, Depends(get_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[
        datetime | None, Query(description="Cursor: `next_before` of the previous page")
    ] = None,
) -> ConversationPage:
    rows, total = await store.list_page(limit=limit, before=before)
    items = [ConversationSummary.model_validate(r, from_attributes=True) for r in rows]
    next_before = items[-1].updated_at if len(items) == limit else None
    return ConversationPage(items=items, total=total, next_before=next_before)


@router.get("/{conversation_id}", response_model=ConversationDetail, summary="Full history")
async def get_conversation(
    conversation_id: str,
    store: Annotated[ConversationStore, Depends(get_store)],
    include_context: Annotated[
        bool, Query(description="Include the per-turn context notes Alfred was given")
    ] = False,
) -> ConversationDetail:
    conv = await store.get(conversation_id)
    if conv is None:
        raise HTTPException(404, "conversation not found")
    rows = await store.messages(conversation_id)
    messages = [
        MessageOut.model_validate(r, from_attributes=True)
        for r in rows
        if include_context or r.role != "context"
    ]
    summary = ConversationSummary.model_validate(conv, from_attributes=True)
    return ConversationDetail(**summary.model_dump(), messages=messages)


@router.delete("/{conversation_id}", status_code=204, summary="Delete a conversation")
async def delete_conversation(
    conversation_id: str, store: Annotated[ConversationStore, Depends(get_store)]
) -> Response:
    if not await store.delete(conversation_id):
        raise HTTPException(404, "conversation not found")
    return Response(status_code=204)
