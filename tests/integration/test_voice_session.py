"""Hands-free conversation over `WS /v1/voice`, with synthetic audio and a scripted recogniser."""

from __future__ import annotations

import math
from array import array
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.llm.fake import FakeProvider, Script
from app.main import create_app
from app.voice.fake import FakeTTS
from app.voice.vad import energy_speech_test
from tests.conftest import TOKEN


class ScriptedSTT:
    name = "scripted"

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.heard = 0

    async def transcribe(self, audio: bytes, content_type: str, language: str = "") -> str:
        assert content_type.startswith("audio/pcm")
        self.heard += 1
        return self.lines.pop(0) if self.lines else ""

    async def aclose(self) -> None:
        return None


def speech(seconds: float) -> bytes:
    n = int(16000 * seconds)
    return array(
        "h", (int(9000 * math.sin(2 * math.pi * 170 * i / 16000)) for i in range(n))
    ).tobytes()


def quiet(seconds: float) -> bytes:
    return bytes(int(16000 * seconds) * 2)


@pytest.fixture
def stt() -> ScriptedSTT:
    return ScriptedSTT()


@pytest.fixture
def app_client(
    settings: Settings, fake_llm: FakeProvider, stt: ScriptedSTT
) -> Iterator[TestClient]:
    settings.follow_up_window_s = 0.4
    settings.end_of_speech_ms = 300
    app = create_app(settings, llm=fake_llm, tts=FakeTTS(), stt=stt)
    with TestClient(app) as client:
        app.state.speech_test = energy_speech_test
        yield client


class Voice:
    def __init__(self, ws: WebSocketTestSession) -> None:
        self.ws = ws
        self.seen: list[dict[str, Any]] = []
        self.claimed: set[int] = set()

    def say(self, seconds: float = 0.6) -> None:
        audio = quiet(0.2) + speech(seconds) + quiet(0.5)
        for i in range(0, len(audio), 640):
            self.ws.send_bytes(audio[i : i + 640])

    def control(self, **msg: Any) -> None:
        self.ws.send_json(msg)

    def until(self, pred: Callable[[dict[str, Any]], bool], limit: int = 400) -> dict[str, Any]:
        """First not-yet-claimed event matching `pred`, reading more from the socket as needed."""
        for i, event in enumerate(self.seen):
            if i not in self.claimed and pred(event):
                self.claimed.add(i)
                return event
        for _ in range(limit):
            event = self.ws.receive_json()
            self.seen.append(event)
            if pred(event):
                self.claimed.add(len(self.seen) - 1)
                return event
        raise AssertionError(f"event not seen; got {[e['type'] for e in self.seen[-20:]]}")

    def state(self, value: str) -> dict[str, Any]:
        return self.until(lambda e: e["type"] == "state" and e["data"]["value"] == value)

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.seen if e["type"] == kind]


@contextmanager
def connect(client: TestClient, mode: str = "wake") -> Iterator[Voice]:
    with client.websocket_connect("/v1/voice", subprotocols=["bearer", TOKEN]) as ws:
        ws.send_json({"type": "hello", "mode": mode, "sample_rate": 16000})
        voice = Voice(ws)
        ready = voice.until(lambda e: e["type"] == "ready")
        assert ready["data"]["wake_word"] == "Alfred"
        voice.state("idle")  # initial state
        yield voice


def test_asleep_ignores_speech_until_addressed(
    app_client: TestClient, stt: ScriptedSTT, fake_llm: FakeProvider
) -> None:
    stt.lines = ["The weather is nice today.", "Alfred, is the car ready?"]
    fake_llm.scripts = [Script(text="It is, sir. Shall I bring it round?")]
    with connect(app_client) as voice:
        voice.say()  # not addressed to Alfred
        voice.say()  # addressed
        heard = voice.until(lambda e: e["type"] == "transcript")
        assert heard["data"] == {
            "text": "Alfred, is the car ready?",
            "command": "Is the car ready?",
        }
        voice.state("thinking")
        voice.state("speaking")
        voice.until(lambda e: e["type"] == "done")
        assert len(voice.of_type("transcript")) == 1  # the first remark was dropped
        assert fake_llm.requests[0].messages[0].text == "Is the car ready?"
        assert len(voice.of_type("audio")) == 2

        voice.control(type="playback", state="ended")
        voice.state("listening")  # follow-up window
        voice.state("idle")  # window expired: asleep again


def test_name_alone_gets_yes_sir_then_follow_up_needs_no_name(
    app_client: TestClient, stt: ScriptedSTT, fake_llm: FakeProvider
) -> None:
    stt.lines = ["Alfred?", "What's on today?"]
    fake_llm.scripts = [Script(text="Very little, sir.")]
    with connect(app_client) as voice:
        voice.say()
        ack_heard = voice.until(lambda e: e["type"] == "transcript")
        assert ack_heard["data"] == {"text": "Alfred?", "command": ""}
        voice.until(lambda e: e["type"] == "token" and e["data"]["text"] == "Yes, sir?")
        ack = voice.until(lambda e: e["type"] == "done")
        assert ack["data"]["status"] == "ack"
        voice.control(type="playback", state="ended")
        voice.state("listening")
        voice.say()
        heard = voice.until(lambda e: e["type"] == "transcript")
        assert heard["data"]["command"] == "What's on today?"
        voice.until(lambda e: e["type"] == "done")


def test_talking_over_alfred_interrupts_him(
    app_client: TestClient, stt: ScriptedSTT, fake_llm: FakeProvider
) -> None:
    stt.lines = ["Alfred, tell me about the house.", "Actually, stop. What time is it?"]
    fake_llm.scripts = [
        Script(text="The house has twelve rooms. " * 6),
        Script(text="It is nine o'clock, sir."),
    ]
    with connect(app_client) as voice:
        voice.say()
        voice.until(lambda e: e["type"] == "transcript")
        voice.state("speaking")
        voice.say(seconds=1.6)  # long enough to count as a real interruption
        voice.until(lambda e: e["type"] == "duck")
        interrupt = voice.until(lambda e: e["type"] == "interrupt")
        assert interrupt["data"]["reason"] == "speech"
        heard = voice.until(lambda e: e["type"] == "transcript")
        assert heard["data"]["command"] == "Actually, stop. What time is it?"
        voice.until(lambda e: e["type"] == "done" and e["data"]["status"] == "complete")
        # Alfred is told he was cut off mid-speech
        history = app_client.get(
            f"/v1/conversations/{heard['conversation_id']}?include_context=true",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()["messages"]
        assert any("interrupted Alfred while he was still speaking" in m["text"] for m in history)


def test_interrupting_while_he_thinks_keeps_the_partial_reply(
    app_client: TestClient, stt: ScriptedSTT, fake_llm: FakeProvider
) -> None:
    stt.lines = ["Alfred, think hard about this.", "Never mind."]
    fake_llm.scripts = [Script(text="Let me consider", delay_s=3.0), Script(text="Very good, sir.")]
    with connect(app_client) as voice:
        voice.say()
        voice.until(lambda e: e["type"] == "transcript")
        voice.state("thinking")
        voice.say(seconds=1.6)
        voice.until(lambda e: e["type"] == "interrupt")
        heard = voice.until(lambda e: e["type"] == "transcript")
        assert heard["data"]["command"] == "Never mind."
        voice.until(lambda e: e["type"] == "done" and e["data"]["status"] == "complete")
        history = app_client.get(
            f"/v1/conversations/{heard['conversation_id']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()["messages"]
        assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
        assert history[1]["text"] == "[interrupted by the owner]"


def test_own_voice_echo_does_not_interrupt(
    app_client: TestClient, stt: ScriptedSTT, fake_llm: FakeProvider
) -> None:
    stt.lines = ["Alfred, status report.", "The house is quiet sir"]
    fake_llm.scripts = [Script(text="The house is quiet, sir. All doors are locked.")]
    with connect(app_client) as voice:
        voice.say()
        voice.state("speaking")
        voice.say(seconds=0.5)  # Alfred hearing himself through the speakers
        voice.until(lambda e: e["type"] == "unduck")
        assert not voice.of_type("interrupt")
        voice.control(type="playback", state="ended")
        voice.state("listening")


def test_typed_message_and_tap(app_client: TestClient, stt: ScriptedSTT) -> None:
    with connect(app_client) as voice:
        voice.control(type="text", message="Good evening")
        heard = voice.until(lambda e: e["type"] == "transcript")
        assert heard["data"]["command"] == "Good evening"
        voice.until(lambda e: e["type"] == "done")
        voice.control(type="playback", state="ended")
        voice.state("listening")
        voice.state("idle")
        voice.control(type="wake")  # tap: listen without the name
        voice.state("listening")


def test_push_to_talk_hears_nothing_until_tapped(app_client: TestClient, stt: ScriptedSTT) -> None:
    stt.lines = ["Alfred, are you there?", "Lights off please."]
    with connect(app_client, mode="ptt") as voice:
        voice.say()  # ignored entirely: not even transcribed
        voice.control(type="wake")
        voice.state("listening")
        voice.say()
        heard = voice.until(lambda e: e["type"] == "transcript")
        assert heard["data"]["text"] == "Alfred, are you there?"
        assert stt.heard == 1


def test_rejects_bad_token(app_client: TestClient) -> None:
    with app_client.websocket_connect("/v1/voice", subprotocols=["bearer", "nope"]) as ws:
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_json()
        assert info.value.code == 4401


def test_ask_endpoint_for_watches_and_shortcuts(
    app_client: TestClient, fake_llm: FakeProvider
) -> None:
    auth = {"Authorization": f"Bearer {TOKEN}"}
    fake_llm.scripts = [Script(text="**Twelve** degrees, sir."), Script(text="Yes, sir.")]
    first = app_client.post("/v1/ask", json={"message": "Weather?", "voice": True}, headers=auth)
    body = first.json()
    assert body["reply"] == "**Twelve** degrees, sir." and body["audio_b64"]
    assert body["audio_media_type"] == "audio/wav"
    # "latest" (the default) continues the same conversation
    second = app_client.post("/v1/ask", json={"message": "Umbrella?"}, headers=auth).json()
    assert second["conversation_id"] == body["conversation_id"] and second["audio_b64"] is None
    fresh = app_client.post(
        "/v1/ask", json={"message": "Hi", "conversation_id": None}, headers=auth
    ).json()
    assert fresh["conversation_id"] != body["conversation_id"]
    assert app_client.post("/v1/ask", json={"message": "x"}).status_code == 401
