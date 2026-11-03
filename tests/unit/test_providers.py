"""Provider adapters tested against recorded-shape wire traffic (no network)."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import openai
import pytest

from app.llm.anthropic import AnthropicProvider
from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMRefusal,
    LLMRequest,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from app.llm.fake import FakeProvider, Script
from app.llm.local import LocalProvider
from app.llm.router import FailoverProvider


def sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def anthropic_stream(text_chunks: list[str], stop_reason: str = "end_turn") -> bytes:
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 100,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig=="},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    events += [
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": chunk},
            },
        )
        for chunk in text_chunks
    ]
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 42},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return sse(events)


class Recorder:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx2.Response(
            self.status, content=self.body, headers={"content-type": "text/event-stream"}
        )


def make_anthropic(recorder: Recorder, model: str = "claude-opus-5-5") -> AnthropicProvider:
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return AnthropicProvider("sk-ant-test", model, client=client)


HISTORY = [
    ChatMessage(role="user", text="Hello"),
    ChatMessage(role="context", text="Current date and time: now"),
    ChatMessage(
        role="assistant",
        text="Good evening, sir.",
        native={
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s1"},
                {"type": "text", "text": "Good evening, sir."},
            ],
        },
    ),
    ChatMessage(role="user", text="Weather?"),
    ChatMessage(role="context", text="Current date and time: later"),
]


async def collect(provider: Any, request: LLMRequest) -> tuple[str, StreamEnd]:
    text, end = "", None
    async for event in provider.stream(request):
        if isinstance(event, TextDelta):
            text += event.text
        else:
            end = event
    assert end is not None
    return text, end


async def test_anthropic_streams_and_replays_native_content() -> None:
    rec = Recorder(anthropic_stream(["Very ", "good, ", "sir."]))
    provider = make_anthropic(rec)
    text, end = await collect(provider, LLMRequest(system="SYS", messages=HISTORY, effort="low"))

    assert text == "Very good, sir."
    assert end.usage.input_tokens == 120 and end.usage.output_tokens == 42
    assert end.usage.cache_read_tokens == 100
    assert end.message.native and end.message.native["content"][0]["type"] == "thinking"

    body = rec.requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in body
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in rec.headers[0]["anthropic-beta"]
    roles = [m["role"] for m in body["messages"]]
    # context notes become mid-conversation system messages after each user turn
    assert roles == ["user", "system", "assistant", "user", "system"]
    # previous assistant turn is replayed verbatim, thinking block included
    assert body["messages"][2]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "s1",
    }


async def test_anthropic_folds_context_for_models_without_mid_system() -> None:
    rec = Recorder(anthropic_stream(["ok"]))
    provider = make_anthropic(rec, model="claude-sonnet-4-6")
    await collect(provider, LLMRequest(system="S", messages=HISTORY))
    body = rec.requests[0]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert "turn_context" in body["messages"][2]["content"][1]["text"]
    assert "fallbacks" not in body


async def test_anthropic_tools_use_auto_choice() -> None:
    rec = Recorder(anthropic_stream(["ok"]))
    provider = make_anthropic(rec)
    tool = ToolSpec("get_datetime", "Current time", {"type": "object", "properties": {}})
    msgs = [
        ChatMessage(role="user", text="time?"),
        ChatMessage(role="assistant", tool_calls=[ToolCall("t1", "get_datetime", {})]),
        ChatMessage(role="tool", tool_results=[ToolResult("t1", "23:00")]),
    ]
    await collect(provider, LLMRequest(system="S", messages=msgs, tools=[tool]))
    body = rec.requests[0]
    assert body["tool_choice"] == {"type": "auto"}
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "23:00",
        "is_error": False,
    }


async def test_anthropic_refusal_raises() -> None:
    provider = make_anthropic(Recorder(anthropic_stream([], stop_reason="refusal")))
    with pytest.raises(LLMRefusal):
        await collect(provider, LLMRequest(system="S", messages=HISTORY[:2]))


async def test_anthropic_server_error_is_retryable() -> None:
    body = json.dumps(
        {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    ).encode()
    provider = make_anthropic(Recorder(body, status=529))
    with pytest.raises(LLMError) as info:
        await collect(provider, LLMRequest(system="S", messages=HISTORY[:2]))
    assert info.value.retryable


def openai_chunks(chunks: list[dict[str, Any]]) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def chunk(delta: dict[str, Any], finish: str | None = None, usage: Any = None) -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gemma3:12b",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]
        if delta is not None
        else [],
        "usage": usage,
    }


async def test_local_ollama_provider_text_and_tool_calls() -> None:
    body = openai_chunks(
        [
            chunk({"role": "assistant", "content": "Checking"}),
            chunk({"content": ", sir."}),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "get_datetime", "arguments": '{"tz":'},
                        }
                    ]
                }
            ),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"Europe/Berlin"}'}}]}),
            chunk({}, finish="tool_calls"),
            chunk(None, usage={"prompt_tokens": 50, "completion_tokens": 9, "total_tokens": 59}),
        ]
    )
    rec = Recorder(body)
    client = openai.AsyncOpenAI(
        api_key="local",
        base_url="http://localhost:11434/v1",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(rec)),
    )
    provider = LocalProvider("http://localhost:11434/v1", client=client)
    assert provider.model == "gemma3:12b"
    text, end = await collect(provider, LLMRequest(system="SYS", messages=HISTORY))
    assert text == "Checking, sir."
    assert end.message.tool_calls == [ToolCall("call_a", "get_datetime", {"tz": "Europe/Berlin"})]
    assert end.usage.input_tokens == 50 and end.stop_reason == "tool_use"
    sent = rec.requests[0]
    # capped to a quarter of the 8k local window, and named the way Ollama expects
    assert sent["max_tokens"] == 2048 and "max_completion_tokens" not in sent
    assert [m["role"] for m in sent["messages"]] == [
        "system",
        "user",
        "system",
        "assistant",
        "user",
        "system",
    ]


async def test_failover_on_retryable_error_before_output() -> None:
    primary = FakeProvider([Script(error=LLMError("down", code="llm_unreachable", retryable=True))])
    fallback = FakeProvider([Script(text="From the reserve.")], model="fallback-1")
    router = FailoverProvider(primary, fallback)
    text, end = await collect(router, LLMRequest(system="s", messages=HISTORY[:1]))
    assert text == "From the reserve." and end.model == "fallback-1"
    assert router.last_used_fallback


async def test_no_failover_for_non_retryable_errors() -> None:
    primary = FakeProvider([Script(error=LLMError("bad key", code="llm_auth"))])
    router = FailoverProvider(primary, FakeProvider())
    with pytest.raises(LLMError):
        await collect(router, LLMRequest(system="s", messages=HISTORY[:1]))
