"""Typed event stream shared by SSE, WebSocket and the proactive event bus."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "token",
    "tool_call",
    "tool_result",
    "confirm_request",
    "action_resolved",
    "proactive",
    "state",
    "audio",
    "error",
    "done",
]

AgentState = Literal["idle", "listening", "thinking", "speaking", "awaiting_confirmation"]


def _now() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    type: EventType
    conversation_id: str | None = None
    timestamp: datetime = Field(default_factory=_now)
    seq: int = 0
    data: dict[str, Any] = Field(default_factory=dict)

    def sse(self) -> dict[str, str]:
        """Payload for sse-starlette: `event:` line = type, `data:` line = full JSON."""
        return {"event": self.type, "data": self.model_dump_json()}


# --- constructors (keep payload shapes in one place) ---------------------------------------


def token(text: str) -> Event:
    return Event(type="token", data={"text": text})


def state(value: AgentState) -> Event:
    return Event(type="state", data={"value": value})


def error(code: str, message: str) -> Event:
    return Event(type="error", data={"code": code, "message": message})


def done(
    *,
    status: str,
    usage: dict[str, int],
    cost_usd: float,
    model: str,
    provider: str,
    message_id: str | None,
    used_fallback: bool = False,
    conversation_totals: dict[str, Any] | None = None,
) -> Event:
    return Event(
        type="done",
        data={
            "status": status,
            "message_id": message_id,
            "provider": provider,
            "model": model,
            "used_fallback": used_fallback,
            "usage": usage,
            "cost_usd": round(cost_usd, 6),
            "conversation_totals": conversation_totals or {},
        },
    )
