"""Conversation persistence (append-only message log)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation, Message
from app.llm.base import ChatMessage, ToolCall, ToolResult, Usage


def _to_row_content(msg: ChatMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if msg.text:
        blocks.append({"type": "text", "text": msg.text})
    blocks += [{"type": "tool_call", **asdict(c)} for c in msg.tool_calls]
    blocks += [{"type": "tool_result", **asdict(r)} for r in msg.tool_results]
    return blocks


def row_to_chat(row: Message) -> ChatMessage:
    calls, results, text = [], [], []
    for b in row.content or []:
        kind = b.get("type")
        if kind == "text":
            text.append(b["text"])
        elif kind == "tool_call":
            calls.append(ToolCall(id=b["id"], name=b["name"], arguments=b["arguments"]))
        elif kind == "tool_result":
            results.append(
                ToolResult(
                    tool_call_id=b["tool_call_id"], content=b["content"], is_error=b["is_error"]
                )
            )
    return ChatMessage(
        role=row.role,  # type: ignore[arg-type]
        text="".join(text),
        tool_calls=calls,
        tool_results=results,
        native=row.native,
    )


class ConversationStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create(self, title: str = "") -> Conversation:
        async with self.sessions() as s:
            conv = Conversation(title=title[:200])
            s.add(conv)
            await s.commit()
            return conv

    async def get(self, conversation_id: str) -> Conversation | None:
        async with self.sessions() as s:
            return await s.get(Conversation, conversation_id)

    async def list_page(
        self, *, limit: int = 20, before: datetime | None = None
    ) -> tuple[list[Conversation], int]:
        async with self.sessions() as s:
            q = select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
            if before is not None:
                q = q.where(Conversation.updated_at < before)
            rows = (await s.scalars(q)).all()
            total = await s.scalar(select(func.count()).select_from(Conversation))
            return list(rows), int(total or 0)

    async def delete(self, conversation_id: str) -> bool:
        async with self.sessions() as s:
            result = await s.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await s.commit()
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def append(
        self, conversation_id: str, msg: ChatMessage, usage: dict[str, Any] | None = None
    ) -> Message:
        async with self.sessions() as s:
            seq = await s.scalar(
                select(func.coalesce(func.max(Message.seq), 0)).where(
                    Message.conversation_id == conversation_id
                )
            )
            row = Message(
                conversation_id=conversation_id,
                seq=int(seq or 0) + 1,
                role=msg.role,
                content=_to_row_content(msg),
                native=msg.native,
                text=msg.text,
                usage=usage,
            )
            s.add(row)
            conv = await s.get(Conversation, conversation_id)
            if conv is not None:
                conv.updated_at = row.created_at
            await s.commit()
            return row

    async def messages(self, conversation_id: str) -> list[Message]:
        async with self.sessions() as s:
            q = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq)
            )
            return list((await s.scalars(q)).all())

    async def add_usage(self, conversation_id: str, usage: Usage, cost: float) -> Conversation:
        async with self.sessions() as s:
            conv = await s.get(Conversation, conversation_id)
            assert conv is not None
            conv.input_tokens += usage.input_tokens + usage.cache_read_tokens
            conv.output_tokens += usage.output_tokens
            conv.cost_usd += cost
            await s.commit()
            return conv
