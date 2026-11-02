"""The agent loop: context → LLM → (tools, from M2) → answer, as a typed event stream."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from datetime import datetime

from app.config import Settings
from app.core import events as ev
from app.core.context import build_window
from app.core.events import Event
from app.core.persona import OutputMode, OwnerInfo, Persona
from app.core.tokens import cost_usd
from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMRequest,
    StreamEnd,
    TextDelta,
    Usage,
)
from app.memory.short_term import ConversationStore, row_to_chat

log = logging.getLogger(__name__)

NO_LLM_MESSAGE = (
    "No language model is configured. Set ANTHROPIC_API_KEY (or OPENAI_API_KEY + LLM_MODEL, "
    "or LOCAL_LLM_BASE_URL for Ollama) and set LLM_PROVIDER accordingly."
)


class Agent:
    def __init__(
        self,
        settings: Settings,
        llm: LLMProvider | None,
        persona: Persona,
        store: ConversationStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.persona = persona
        self.store = store
        self._clock = clock or (lambda: datetime.now(settings.tz))
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def owner(self) -> OwnerInfo:
        s = self.settings
        return OwnerInfo(
            name=s.owner_name,
            address_as=s.owner_address_as,
            profile=s.owner_profile,
            timezone=s.owner_timezone,
            location=s.owner_location,
        )

    def enabled_tools(self) -> list[str]:
        return []  # filled by the tool registry in M2

    async def run_turn(
        self, message: str, conversation_id: str | None = None, mode: OutputMode = "text"
    ) -> AsyncIterator[Event]:
        if conversation_id is None:
            conversation_id = (await self.store.create(title=message.strip()[:60])).id
        seq = 0

        def stamp(event: Event) -> Event:
            nonlocal seq
            seq += 1
            event.conversation_id = conversation_id
            event.seq = seq
            return event

        async with self._locks[conversation_id]:
            await self.store.append(conversation_id, ChatMessage(role="user", text=message))
            note = self.persona.turn_context(self._clock(), mode)
            await self.store.append(conversation_id, ChatMessage(role="context", text=note))

            yield stamp(ev.state("thinking"))
            if self.llm is None:
                yield stamp(ev.error("llm_unavailable", NO_LLM_MESSAGE))
                yield stamp(self._done("error", Usage(), 0.0, "", "", None))
                yield stamp(ev.state("idle"))
                return

            async for event in self._loop(conversation_id):
                yield stamp(event)
            yield stamp(ev.state("idle"))

    async def _loop(self, conversation_id: str) -> AsyncIterator[Event]:
        assert self.llm is not None
        total = Usage()
        total_cost = 0.0
        system = self.persona.system_prompt(self.owner(), self.enabled_tools())
        final: StreamEnd | None = None
        try:
            async with asyncio.timeout(self.settings.turn_timeout_s):
                for _step in range(self.settings.max_agent_steps):
                    history = [row_to_chat(r) for r in await self.store.messages(conversation_id)]
                    window = build_window(
                        system,
                        history,
                        budget_tokens=self.settings.context_budget_tokens,
                        max_turns=self.settings.recent_turns,
                    )
                    request = LLMRequest(
                        system=window.system,
                        messages=window.messages,
                        max_output_tokens=self.settings.llm_max_output_tokens,
                        effort=self.settings.llm_effort,
                    )
                    async for item in self.llm.stream(request):
                        if isinstance(item, TextDelta):
                            yield ev.token(item.text)
                        else:
                            final = item
                    if final is None:
                        raise LLMError("stream ended early", code="llm_incomplete")
                    step_cost = cost_usd(final.model, final.usage)
                    total = total + final.usage
                    total_cost += step_cost
                    row = await self.store.append(
                        conversation_id,
                        final.message,
                        usage={**asdict(final.usage), "cost_usd": step_cost, "model": final.model},
                    )
                    # M2: if final.message.tool_calls → run tools, append results, continue.
                    break
        except TimeoutError:
            yield ev.error("turn_timeout", "That took too long; I've stopped. Please try again.")
            yield self._done("error", total, total_cost, "", "", None)
            return
        except LLMError as exc:
            log.warning("LLM failure in conversation %s: %s", conversation_id, exc.code)
            yield ev.error(exc.code, _safe_message(exc))
            if total.input_tokens or total.output_tokens:
                await self.store.add_usage(conversation_id, total, total_cost)
            yield self._done("error", total, total_cost, "", "", None)
            return

        assert final is not None
        conv = await self.store.add_usage(conversation_id, total, total_cost)
        yield self._done(
            "complete",
            total,
            total_cost,
            final.model,
            final.provider,
            row.id,
            used_fallback=bool(getattr(self.llm, "last_used_fallback", False)),
            totals={
                "input_tokens": conv.input_tokens,
                "output_tokens": conv.output_tokens,
                "cost_usd": round(conv.cost_usd, 6),
            },
        )

    @staticmethod
    def _done(
        status: str,
        usage: Usage,
        cost: float,
        model: str,
        provider: str,
        message_id: str | None,
        *,
        used_fallback: bool = False,
        totals: dict[str, object] | None = None,
    ) -> Event:
        return ev.done(
            status=status,
            usage=asdict(usage),
            cost_usd=cost,
            model=model,
            provider=provider,
            message_id=message_id,
            used_fallback=used_fallback,
            conversation_totals=dict(totals or {}),
        )


def _safe_message(exc: LLMError) -> str:
    return {
        "llm_unreachable": "I couldn't reach the language model. Please try again shortly.",
        "llm_rate_limited": "The language model is rate-limiting us. Please try again shortly.",
        "llm_auth": "The language model rejected the configured API key.",
        "llm_refusal": "The model declined to help with that request.",
    }.get(exc.code, "The language model returned an error.")
