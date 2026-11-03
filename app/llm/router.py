"""Provider construction and primary → fallback failover."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.config import Settings
from app.llm.base import LLMError, LLMEvent, LLMProvider, LLMRequest, TextDelta

log = logging.getLogger(__name__)


def build_provider(settings: Settings, which: str, model: str) -> LLMProvider | None:
    """Return a provider, or None if it isn't configured. Never raises for missing keys."""
    if not settings.provider_configured(which):
        return None
    timeout = settings.llm_timeout_s
    if which == "anthropic":
        from app.llm.anthropic import AnthropicProvider

        return AnthropicProvider(
            settings.anthropic_api_key.get_secret_value(),
            model,
            timeout_s=timeout,
            refusal_fallback=settings.anthropic_refusal_fallback,
        )
    if which == "openai":
        from app.llm.openai import OpenAIProvider

        if not model:
            log.warning("LLM provider openai needs LLM_MODEL; disabled")
            return None
        return OpenAIProvider(settings.openai_api_key.get_secret_value(), model, timeout_s=timeout)
    if which == "local":
        from app.llm.local import LocalProvider

        return LocalProvider(
            settings.local_llm_base_url,
            model,
            api_key=settings.local_llm_api_key.get_secret_value(),
            context_tokens=settings.local_llm_context_tokens,
            timeout_s=timeout,
        )
    if which == "fake":
        from app.llm.fake import FakeProvider

        return FakeProvider()
    return None


class FailoverProvider:
    """Wraps a primary and optional fallback provider.

    Fails over only if the primary raised a retryable error *before emitting any text*, so the
    owner never receives a stitched answer from two models.
    """

    supports_tools = True

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.model = primary.model
        self.supports_tools = primary.supports_tools
        self.last_used_fallback = False
        windows = [getattr(p, "context_tokens", None) for p in (primary, fallback) if p]
        self.context_tokens = min((w for w in windows if w), default=None)

    async def aclose(self) -> None:
        await self.primary.aclose()
        if self.fallback is not None:
            await self.fallback.aclose()

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        self.last_used_fallback = False
        emitted = False
        try:
            async for event in self.primary.stream(request):
                if isinstance(event, TextDelta):
                    emitted = True
                yield event
            return
        except LLMError as exc:
            if emitted or not exc.retryable or self.fallback is None:
                raise
            log.warning("primary LLM failed (%s); failing over to %s", exc.code, self.fallback.name)
        self.last_used_fallback = True
        fallback_request = LLMRequest(
            system=request.system,
            messages=request.messages,
            tools=request.tools if self.fallback.supports_tools else [],
            max_output_tokens=request.max_output_tokens,
            effort=request.effort,
        )
        async for event in self.fallback.stream(fallback_request):
            yield event


def build_llm(settings: Settings) -> FailoverProvider | None:
    primary = build_provider(settings, settings.llm_provider, settings.llm_model)
    fallback = None
    if settings.llm_fallback_provider:
        fallback = build_provider(
            settings, settings.llm_fallback_provider, settings.llm_fallback_model
        )
    if primary is None and fallback is not None:
        log.warning(
            "primary LLM provider %r is not configured; using fallback %r",
            settings.llm_provider,
            settings.llm_fallback_provider,
        )
        primary, fallback = fallback, None
    if primary is None:
        return None
    return FailoverProvider(primary, fallback)
