from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.fake import FakeProvider
from app.main import create_app

TOKEN = "test-token-" + "x" * 32


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        alfred_api_token=TOKEN,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'alfred.db'}",
        owner_name="",
        owner_address_as="sir",
        owner_timezone="Europe/Berlin",
        owner_location="Berlin",
        llm_provider="fake",
        stt_provider="none",
        tts_provider="none",
        whisper_model="base",
        home_assistant_token="ha-SECRETSECRETSECRETSECRET",
    )


@pytest.fixture
def fake_llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(settings: Settings, fake_llm: FakeProvider) -> Iterator[TestClient]:
    app = create_app(settings, llm=fake_llm)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def parse_sse(raw: str) -> list[dict[str, Any]]:
    """Parse an SSE body into the JSON payloads of its `data:` lines (pings skipped)."""
    events = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        data = [line[5:].strip() for line in block.split("\n") if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


def chat(client: TestClient, message: str, **body: Any) -> list[dict[str, Any]]:
    with client.stream("POST", "/v1/chat", json={"message": message, **body}) as resp:
        assert resp.status_code == 200, resp.read()
        return parse_sse(resp.read().decode())
