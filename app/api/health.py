from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.config import Settings, get_settings
from app.security.auth import is_owner_request

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness; integration status when authenticated")
async def health(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "ok"}
    if not is_owner_request(request, settings):
        return body
    llm = request.app.state.llm
    body.update(
        version=__version__,
        llm={
            "configured": llm is not None,
            "provider": getattr(llm, "name", None),
            "model": getattr(llm, "model", None),
            "fallback": getattr(getattr(llm, "fallback", None), "name", None),
        },
        voice={
            "tts": getattr(request.app.state.tts, "name", None),
            "stt": getattr(request.app.state.stt, "name", None),
        },
        integrations=settings.integrations(),
    )
    return body
