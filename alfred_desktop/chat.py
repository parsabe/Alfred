"""Text conversations with Alfred from the terminal, and the server health check."""

from __future__ import annotations

import json
from typing import Any

import httpx

from alfred_desktop.config import Config


class ServerError(Exception):
    pass


def client(cfg: Config, timeout: float | None = 20.0) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.server,
        headers={"Authorization": f"Bearer {cfg.token}"},
        timeout=timeout,
        follow_redirects=True,
    )


def health(cfg: Config) -> dict[str, Any]:
    """The server's status; raises ServerError with a human explanation."""
    try:
        with client(cfg, timeout=10) as c:
            resp = c.get("/health")
    except httpx.HTTPError as exc:
        raise ServerError(f"can't reach {cfg.server} ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise ServerError(f"{cfg.server} answered HTTP {resp.status_code}; is this Alfred?")
    try:
        body: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise ServerError(f"{cfg.server} doesn't look like an Alfred server") from exc
    if "llm" not in body:
        raise ServerError("the server is reachable but rejected the access token")
    return body


def describe(body: dict[str, Any]) -> str:
    llm = body.get("llm", {})
    voice = body.get("voice", {})
    model = llm.get("model") if llm.get("configured") else "no model configured"
    hearing = f"{voice.get('stt')} ({voice.get('stt_status')})" if voice.get("stt") else "off"
    speaking = f"{voice.get('tts')} ({voice.get('tts_status')})" if voice.get("tts") else "off"
    return f"model: {model} · hearing: {hearing} · voice: {speaking}"


def ask(cfg: Config, message: str, conversation_id: str | None, out: Any = print) -> str | None:
    body = {"message": message, "conversation_id": conversation_id}
    with client(cfg, timeout=None) as c, c.stream("POST", "/v1/chat", json=body) as resp:
        if resp.status_code == 401:
            raise ServerError("the server rejected the access token")
        if resp.status_code == 404 and conversation_id:
            return ask(cfg, message, None, out)
        resp.raise_for_status()
        out("Alfred: ", end="", flush=True)
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            conversation_id = event.get("conversation_id") or conversation_id
            if event["type"] == "token":
                out(event["data"]["text"], end="", flush=True)
            elif event["type"] == "error":
                out(f"\n[{event['data']['code']}] {event['data']['message']}", end="")
        out()
    return conversation_id


def repl(cfg: Config) -> int:
    print("Talk to Alfred. Ctrl-D (Ctrl-Z on Windows) to leave.")
    conversation_id = None
    while True:
        try:
            message = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if message:
            conversation_id = ask(cfg, message, conversation_id)
