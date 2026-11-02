"""Bearer-token auth (constant-time) and optional IP allowlist."""

from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status

from app.config import Settings, get_settings


def _client_allowed(host: str | None, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    if host is None:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(cidr, strict=False) for cidr in allowlist)


def token_matches(presented: str, settings: Settings) -> bool:
    expected = settings.alfred_api_token.get_secret_value()
    if not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def _extract_bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


async def require_owner(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    host = request.client.host if request.client else None
    if not _client_allowed(host, settings.ip_allowlist):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if not token_matches(_extract_bearer(request.headers.get("authorization")), settings):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def is_owner_request(request: Request, settings: Settings) -> bool:
    """Non-raising variant, for endpoints that show more detail when authenticated."""
    host = request.client.host if request.client else None
    return _client_allowed(host, settings.ip_allowlist) and token_matches(
        _extract_bearer(request.headers.get("authorization")), settings
    )


def websocket_token(ws: WebSocket) -> str:
    """WS clients send `Sec-WebSocket-Protocol: bearer, <token>` (browsers can't set headers)."""
    header = _extract_bearer(ws.headers.get("authorization"))
    if header:
        return header
    protocols = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
    if len(protocols) == 2 and protocols[0].lower() == "bearer":
        return protocols[1]
    return ""


OwnerDep = Depends(require_owner)
