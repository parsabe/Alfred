"""FastAPI app factory and lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import chat, conversations, health, voice
from app.config import ROOT_DIR, Settings, get_settings
from app.core import tokens
from app.core.agent import Agent
from app.core.persona import Persona
from app.db import migrate
from app.db.session import make_engine, make_sessionmaker
from app.llm.base import LLMProvider
from app.llm.router import build_llm
from app.memory.short_term import ConversationStore
from app.security import redaction
from app.voice.base import STTProvider, TTSProvider

WEB_DIR = ROOT_DIR / "web"


def build_voice(settings: Settings) -> tuple[TTSProvider | None, STTProvider | None]:
    if not settings.voice_enabled:
        return None, None
    from app.voice.fish import FishSTT, FishTTS

    key = settings.fish_audio_api_key.get_secret_value()
    return (
        FishTTS(
            key,
            settings.fish_audio_voice_id,
            settings.fish_audio_model,
            audio_format=settings.fish_audio_format,
        ),
        FishSTT(key),
    )


log = logging.getLogger("alfred")


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    tts: TTSProvider | None = None,
    stt: STTProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redaction.install(
            redaction.Redactor(settings.secret_values()), settings.log_level, settings.log_json
        )
        errors = settings.validate_for_startup()
        if errors:
            for message in errors:
                log.error("config: %s", message)
            raise RuntimeError("invalid configuration: " + "; ".join(errors))
        tokens.load_overrides(settings.llm_price_overrides)

        await migrate.upgrade(settings.database_url)
        engine = make_engine(settings.database_url)
        store = ConversationStore(make_sessionmaker(engine))
        provider = llm if llm is not None else build_llm(settings)
        if provider is None:
            log.warning("no LLM provider configured; chat will return llm_unavailable")
        else:
            log.info("LLM: %s / %s", provider.name, provider.model)

        built_tts, built_stt = build_voice(settings)
        app.state.tts = tts if tts is not None else built_tts
        app.state.stt = stt if stt is not None else built_stt
        log.info("voice: %s", "on" if app.state.tts is not None else "off (no FISH_AUDIO_API_KEY)")

        app.state.settings = settings
        app.state.engine = engine
        app.state.store = store
        app.state.llm = provider
        app.state.agent = Agent(settings, provider, Persona(settings.persona_path), store)
        try:
            yield
        finally:
            for closable in (provider, app.state.tts, app.state.stt):
                if closable is not None:
                    await closable.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Alfred",
        version=__version__,
        summary="Personal assistant backend. All /v1 routes need `Authorization: Bearer <token>`.",
        lifespan=lifespan,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(voice.router)
    if settings.web_ui_enabled and WEB_DIR.is_dir():
        # The ribbon interface. Static and public; every API call it makes needs the token.
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
