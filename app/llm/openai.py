"""OpenAI provider, also used for any OpenAI-compatible server (Ollama, vLLM, LM Studio)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import openai

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

log = logging.getLogger(__name__)


class OpenAIProvider:
    name = "openai"
    # Max-output parameter name; OpenAI-compatible local servers usually only know `max_tokens`.
    max_tokens_param = "max_completion_tokens"

    def __init__(
        self,
        api_key: str,
        model: str = "",
        *,
        base_url: str | None = None,
        timeout_s: float = 120.0,
        supports_tools: bool = True,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        if not model:
            raise ValueError(f"LLM_MODEL must be set for the {self.name} provider")
        self.model = model
        self.supports_tools = supports_tools
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key or "not-needed", base_url=base_url, timeout=timeout_s, max_retries=2
        )

    async def aclose(self) -> None:
        await self._client.close()

    def _messages(self, req: LLMRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": req.system}]
        for m in req.messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "context":
                out.append({"role": "system", "content": m.text})
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ]
                if msg["content"] is None and "tool_calls" not in msg:
                    msg["content"] = ""
                out.append(msg)
            elif m.role == "tool":
                out += [
                    {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                    for r in m.tool_results
                ]
        return out

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        params: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": self._messages(request),
            "stream": True,
            "stream_options": {"include_usage": True},
            self.max_tokens_param: min(
                request.max_output_tokens,
                int(getattr(self, "context_tokens", 0) * 0.25) or request.max_output_tokens,
            ),
        }
        if request.tools and self.supports_tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish = "stop"
        model = params["model"]
        try:
            stream = await self._client.chat.completions.create(**params)
            async for chunk in stream:
                model = chunk.model or model
                if chunk.usage:
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta.content:
                        text_parts.append(delta.content)
                        yield TextDelta(delta.content)
                    for tc in delta.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.APIConnectionError as exc:
            raise LLMError(
                f"could not reach {self.name} model server", code="llm_unreachable", retryable=True
            ) from exc
        except openai.RateLimitError as exc:
            raise LLMError("rate limited", code="llm_rate_limited", retryable=True) from exc
        except openai.AuthenticationError as exc:
            raise LLMError("API key rejected", code="llm_auth") from exc
        except openai.APIStatusError as exc:
            raise LLMError(
                f"{self.name} error {exc.status_code}",
                code="llm_upstream",
                retryable=exc.status_code >= 500,
            ) from exc

        tool_calls = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {"_invalid_json": slot["args"]}
            tool_calls.append(
                ToolCall(id=slot["id"] or f"call_{index}", name=slot["name"], arguments=args)
            )
        yield StreamEnd(
            message=ChatMessage(role="assistant", text="".join(text_parts), tool_calls=tool_calls),
            usage=usage,
            stop_reason="tool_use"
            if tool_calls
            else ("max_tokens" if finish == "length" else "end_turn"),
            provider=self.name,
            model=model,
        )
