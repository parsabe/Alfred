"""Anthropic Claude provider (official SDK, streaming).

Notes for current models (Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.1):
- adaptive thinking; effort via `output_config.effort`; no sampling params; no prefill;
- `tool_choice` stays `auto` (forced tool choice is rejected);
- assistant content (incl. thinking blocks) is replayed verbatim, history is append-only;
- per-turn context goes in a mid-conversation `system` message so the cached prefix survives;
- refusals: server-side `fallbacks: "default"` (beta) on models that accept it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMEvent,
    LLMRefusal,
    LLMRequest,
    StreamEnd,
    TextDelta,
    ToolCall,
    Usage,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"

# Models that accept `role: "system"` entries inside `messages`.
_MID_SYSTEM = (
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
)
# Models that accept the server-side refusal fallback in its "default" form.
_FALLBACK_DEFAULT = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _model_in(model: str, family: tuple[str, ...]) -> bool:
    return model in family


class AnthropicProvider:
    name = "anthropic"
    supports_tools = True

    def __init__(
        self,
        api_key: str,
        model: str = "",
        *,
        timeout_s: float = 120.0,
        refusal_fallback: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_s, max_retries=2
        )
        self._refusal_fallback = refusal_fallback and _model_in(self.model, _FALLBACK_DEFAULT)

    async def aclose(self) -> None:
        await self._client.close()

    # --- conversion --------------------------------------------------------------------

    def _messages(self, msgs: list[ChatMessage]) -> list[dict[str, Any]]:
        mid_system = _model_in(self.model, _MID_SYSTEM)
        out: list[dict[str, Any]] = []
        for m in msgs:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "context":
                if mid_system and out and out[-1]["role"] == "user":
                    out.append({"role": "system", "content": m.text})
                else:
                    # Fold into the preceding user turn (still append-only).
                    self._append_user_text(out, f"<turn_context>\n{m.text}\n</turn_context>")
            elif m.role == "assistant":
                native = m.native or {}
                if native.get("provider") == "anthropic" and native.get("content"):
                    out.append({"role": "assistant", "content": native["content"]})
                else:
                    blocks: list[dict[str, Any]] = []
                    if m.text:
                        blocks.append({"type": "text", "text": m.text})
                    blocks += [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                        for c in m.tool_calls
                    ]
                    out.append({"role": "assistant", "content": blocks or m.text or "…"})
            elif m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.tool_call_id,
                                "content": r.content,
                                "is_error": r.is_error,
                            }
                            for r in m.tool_results
                        ],
                    }
                )
        return out

    @staticmethod
    def _append_user_text(out: list[dict[str, Any]], text: str) -> None:
        if out and out[-1]["role"] == "user":
            content = out[-1]["content"]
            if isinstance(content, str):
                out[-1]["content"] = [{"type": "text", "text": content}]
            out[-1]["content"].append({"type": "text", "text": text})
        else:
            out.append({"role": "user", "content": text})

    def _params(self, req: LLMRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": req.model or self.model,
            "max_tokens": req.max_output_tokens,
            "system": [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": self._messages(req.messages),
            "thinking": {"type": "adaptive"},
        }
        if req.effort:
            params["output_config"] = {"effort": req.effort}
        if req.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in req.tools
            ]
            params["tool_choice"] = {"type": "auto"}
        return params

    # --- streaming ---------------------------------------------------------------------

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        params = self._params(request)
        cm: Any
        try:
            if self._refusal_fallback:
                cm = self._client.beta.messages.stream(
                    **params, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                cm = self._client.messages.stream(**params)
            async with cm as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                final = await stream.get_final_message()
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise LLMError(
                "could not reach Anthropic", code="llm_unreachable", retryable=True
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit", code="llm_rate_limited", retryable=True) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key", code="llm_auth") from exc
        except anthropic.BadRequestError as exc:
            log.warning("anthropic bad request: %s", exc.message)
            raise LLMError("Anthropic rejected the request", code="llm_bad_request") from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code == 529
            raise LLMError(
                f"Anthropic error {exc.status_code}", code="llm_upstream", retryable=retryable
            ) from exc

        if final.stop_reason == "refusal":
            raise LLMRefusal()

        content = [block.model_dump(mode="json", exclude_none=True) for block in final.content]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [
            ToolCall(
                id=b["id"],
                name=b["name"],
                arguments=b["input"]
                if isinstance(b.get("input"), dict)
                else _loads(b.get("input")),
            )
            for b in content
            if b.get("type") == "tool_use"
        ]
        u = final.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        message = ChatMessage(
            role="assistant",
            text=text,
            tool_calls=calls,
            native={"provider": "anthropic", "model": final.model, "content": content},
        )
        yield StreamEnd(
            message=message,
            usage=usage,
            stop_reason=final.stop_reason or "end_turn",
            provider=self.name,
            model=final.model,
        )


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
