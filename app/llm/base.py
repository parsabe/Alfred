"""Provider-neutral LLM types and the provider protocol.

The agent only ever sees these types. Each provider converts to and from its own wire format.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant", "tool", "context"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ChatMessage:
    """A message in provider-neutral form.

    - user / context: `text`
    - assistant: `text` + `tool_calls`; `native` carries the provider's exact content blocks
      (including thinking) for verbatim replay to the same provider.
    - tool: `tool_results`
    """

    role: Role
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    native: dict[str, Any] | None = None


@dataclass
class LLMRequest:
    system: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    max_output_tokens: int = 16000
    effort: str = ""
    model: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


# --- Stream events -------------------------------------------------------------------------


@dataclass
class TextDelta:
    text: str
    type: Literal["text"] = "text"


@dataclass
class StreamEnd:
    """Final event of every successful stream."""

    message: ChatMessage
    usage: Usage
    stop_reason: str
    provider: str
    model: str
    type: Literal["end"] = "end"


LLMEvent = TextDelta | StreamEnd


class LLMError(Exception):
    """Provider failure. `retryable` drives failover; `code` is safe to show the owner."""

    def __init__(self, message: str, *, code: str = "llm_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMRefusal(LLMError):
    def __init__(self, message: str = "The model declined this request.") -> None:
        super().__init__(message, code="llm_refusal", retryable=False)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    supports_tools: bool

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        """Yield TextDelta events, then exactly one StreamEnd. Raise LLMError on failure."""
        ...

    async def aclose(self) -> None: ...


async def complete(provider: LLMProvider, request: LLMRequest) -> StreamEnd:
    """Non-streaming convenience built on `stream`."""
    end: StreamEnd | None = None
    async for event in provider.stream(request):
        if isinstance(event, StreamEnd):
            end = event
    if end is None:
        raise LLMError("stream ended without a final message", code="llm_incomplete")
    return end
