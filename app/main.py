"""FastAPI app factory and lifespan."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import ask, chat, conversations, health, voice, voice_ws
from app.config import Settings, get_settings
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


def build_voice(settings: Settings) -> tuple[TTSProvider | None, STTProvider | None]:
    """Local Whisper + Piper by default; Fish Audio only if explicitly chosen and keyed."""
    tts: TTSProvider | None = None
    stt: STTProvider | None = None
    if settings.tts_provider == "piper":
        from app.voice.local import PiperTTS

        tts = PiperTTS(
            settings.piper_voice, models_dir=settings.models_dir, speed=settings.piper_speed
        )
    elif settings.tts_provider == "fish" and settings.fish_configured:
        from app.voice.fish import FishTTS

        tts = FishTTS(
            settings.fish_audio_api_key.get_secret_value(),
            settings.fish_audio_voice_id,
            settings.fish_audio_model,
            audio_format=settings.fish_audio_format,
        )
    if settings.stt_provider == "whisper":
        from app.voice.local import WhisperSTT

        stt = WhisperSTT(
            settings.whisper_model,
            models_dir=settings.models_dir,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            beam_size=settings.whisper_beam_size,
            language=settings.speech_language,
            hint=settings.wake_word,
        )
    elif settings.stt_provider == "fish" and settings.fish_audio_api_key.get_secret_value():
        from app.voice.fish import FishSTT

        stt = FishSTT(settings.fish_audio_api_key.get_secret_value())
    return tts, stt


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
        log.info(
            "voice: hearing=%s speaking=%s",
            getattr(app.state.stt, "name", "off"),
            getattr(app.state.tts, "name", "off"),
        )
        # download/load speech models in the background; the API is usable meanwhile
        warmups = [
            asyncio.create_task(p.warmup())
            for p in (app.state.tts, app.state.stt)
            if p is not None and hasattr(p, "warmup")
        ]

        app.state.settings = settings
        app.state.engine = engine
        app.state.store = store
        app.state.llm = provider
        app.state.agent = Agent(settings, provider, Persona(settings.persona_path), store)
        try:
            yield
        finally:
            for task in warmups:
                task.cancel()
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
    app.include_router(ask.router)
    app.include_router(voice_ws.router)
    if settings.web_ui_enabled and (settings.web_dir / "index.html").is_file():
        # The ribbon interface (web branch). Static and public; its API calls need the token.
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    else:
        log.info("no web interface in %s (run `make web`); serving the API only", settings.web_dir)
    return app
