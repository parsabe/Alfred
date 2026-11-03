from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.fake import FakeProvider, Script
from app.main import create_app
from app.voice.base import Speech, VoiceError
from app.voice.fake import FakeSTT, FakeTTS
from tests.conftest import TOKEN, chat


@pytest.fixture
def tts() -> FakeTTS:
    return FakeTTS()


@pytest.fixture
def voice_client(settings: Settings, fake_llm: FakeProvider, tts: FakeTTS) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=fake_llm, tts=tts, stt=FakeSTT())) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_voice_mode_streams_audio_per_sentence(
    voice_client: TestClient, fake_llm: FakeProvider, tts: FakeTTS
) -> None:
    fake_llm.scripts = [Script(text="Good evening, sir. The car is ready. Shall I bring it round?")]
    events = chat(voice_client, "Is the car ready?", output_mode="voice")

    audio = [e for e in events if e["type"] == "audio"]
    assert [a["data"]["text"] for a in audio] == [
        "Good evening, sir.",
        "The car is ready.",
        "Shall I bring it round?",
    ]
    assert [a["data"]["index"] for a in audio] == [1, 2, 3]
    assert base64.b64decode(audio[0]["data"]["data_b64"])[:4] == b"RIFF"
    assert audio[0]["data"]["media_type"] == "audio/wav"
    # captions still stream as text, and the turn only ends after the last sentence's audio
    types = [e["type"] for e in events]
    assert "token" in types
    assert types[-2:] == ["done", "state"]
    assert max(i for i, t in enumerate(types) if t == "audio") < types.index("done")
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert len({e["conversation_id"] for e in events}) == 1


def test_text_mode_has_no_audio(voice_client: TestClient) -> None:
    events = chat(voice_client, "Hello")
    assert not any(e["type"] == "audio" for e in events)


def test_voice_failure_falls_back_to_text(settings: Settings, fake_llm: FakeProvider) -> None:
    class BrokenTTS(FakeTTS):
        async def synthesize(self, text: str) -> Speech:
            raise VoiceError("down", code="voice_unreachable", retryable=True)

    fake_llm.scripts = [Script(text="One. Two. Three.")]
    with TestClient(create_app(settings, llm=fake_llm, tts=BrokenTTS())) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        events = chat(c, "Count", output_mode="voice")
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["data"]["code"] == "voice_unreachable"
    assert "".join(e["data"]["text"] for e in events if e["type"] == "token") == "One. Two. Three."
    assert events[-2]["data"]["status"] == "complete"


def test_voice_mode_without_voice_configured(settings: Settings, fake_llm: FakeProvider) -> None:
    with TestClient(create_app(settings, llm=fake_llm)) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        voice = c.get("/health").json()["voice"]
        assert voice["tts"] is None and voice["stt"] is None
        events = chat(c, "Hello", output_mode="voice")
        assert events[0]["type"] == "error" and events[0]["data"]["code"] == "voice_unavailable"
        assert c.post("/v1/tts", json={"text": "hi"}).status_code == 503


def test_tts_endpoint(voice_client: TestClient, tts: FakeTTS) -> None:
    resp = voice_client.post("/v1/tts", json={"text": "Very good, sir."})
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav"
    assert tts.spoken == ["Very good, sir."]
    assert voice_client.post("/v1/tts", json={"text": ""}).status_code == 422


def test_stt_endpoint(voice_client: TestClient) -> None:
    resp = voice_client.post(
        "/v1/stt", files={"audio": ("s.webm", b"\x1a\x45\xdf\xa3", "audio/webm")}
    )
    assert resp.status_code == 200 and resp.json() == {"text": "Good evening, Alfred."}
    empty = voice_client.post("/v1/stt", files={"audio": ("s.webm", b"", "audio/webm")})
    assert empty.status_code == 422


def test_voice_endpoints_need_token(voice_client: TestClient) -> None:
    no_auth = {"Authorization": ""}
    assert voice_client.post("/v1/tts", json={"text": "x"}, headers=no_auth).status_code == 401
    assert (
        voice_client.post(
            "/v1/stt", files={"audio": ("a", b"x", "audio/webm")}, headers=no_auth
        ).status_code
        == 401
    )


def test_web_interface_is_served(
    settings: Settings, fake_llm: FakeProvider, tmp_path: Path
) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<canvas id="ribbon"></canvas>')
    (web / "ribbon.js").write_text("export {};")
    settings.web_dir = web
    with TestClient(create_app(settings, llm=fake_llm)) as client:
        page = client.get("/")
        assert page.status_code == 200 and '<canvas id="ribbon"' in page.text
        assert client.get("/ribbon.js").status_code == 200
        # API routes still win over the static mount
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/openapi.json").status_code == 200


def test_api_only_without_web_interface(
    settings: Settings, fake_llm: FakeProvider, tmp_path: Path
) -> None:
    settings.web_dir = tmp_path / "missing"
    with TestClient(create_app(settings, llm=fake_llm)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200
