"""FastAPI app factory and lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import chat, conversations, health
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

log = logging.getLogger("alfred")


def create_app(settings: Settings | None = None, *, llm: LLMProvider | None = None) -> FastAPI:
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

        app.state.settings = settings
        app.state.engine = engine
        app.state.store = store
        app.state.llm = provider
        app.state.agent = Agent(settings, provider, Persona(settings.persona_path), store)
        try:
            yield
        finally:
            if provider is not None:
                await provider.aclose()
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
    return app
