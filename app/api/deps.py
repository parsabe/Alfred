"""Shared FastAPI dependencies: pull long-lived services off `app.state`."""

from __future__ import annotations

from fastapi import Request

from app.core.agent import Agent
from app.memory.short_term import ConversationStore


def get_agent(request: Request) -> Agent:
    return request.app.state.agent  # type: ignore[no-any-return]


def get_store(request: Request) -> ConversationStore:
    return request.app.state.store  # type: ignore[no-any-return]
