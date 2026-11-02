"""Scripted provider for deterministic tests (and a no-key demo mode)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMEvent,
    LLMRequest,
    StreamEnd,
    TextDelta,
    ToolCall,
    Usage,
)


@dataclass
class Script:
    """One scripted model response."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: LLMError | None = None
    delay_s: float = 0.0
    chunk_size: int = 4


Responder = Callable[[LLMRequest], Script]


class FakeProvider:
    name = "fake"
    supports_tools = True

    def __init__(
        self,
        scripts: list[Script] | None = None,
        *,
        responder: Responder | None = None,
        model: str = "fake-1",
    ) -> None:
        self.model = model
        self.scripts = list(scripts or [])
        self.responder = responder
        self.requests: list[LLMRequest] = []

    async def aclose(self) -> None:
        return None

    def _next(self, request: LLMRequest) -> Script:
        if self.responder is not None:
            return self.responder(request)
        if self.scripts:
            return self.scripts.pop(0)
        last_user = next((m.text for m in reversed(request.messages) if m.role == "user"), "")
        return Script(text=f"Very good. You said: {last_user}")

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        self.requests.append(request)
        script = self._next(request)
        if script.delay_s:
            await asyncio.sleep(script.delay_s)
        if script.error is not None:
            raise script.error
        for i in range(0, len(script.text), script.chunk_size):
            yield TextDelta(script.text[i : i + script.chunk_size])
        prompt_chars = len(request.system) + sum(len(m.text) for m in request.messages)
        yield StreamEnd(
            message=ChatMessage(role="assistant", text=script.text, tool_calls=script.tool_calls),
            usage=Usage(
                input_tokens=max(1, prompt_chars // 4), output_tokens=len(script.text) // 4
            ),
            stop_reason="tool_use" if script.tool_calls else "end_turn",
            provider=self.name,
            model=self.model,
        )
