from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.voice.base import VoiceError
from app.voice.chunker import SentenceChunker, clean_for_speech
from app.voice.fish import FishSTT, FishTTS


def feed_all(text: str, step: int = 3) -> list[str]:
    chunker = SentenceChunker()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += chunker.feed(text[i : i + step])
    return out + chunker.flush()


def test_chunker_splits_sentences_as_tokens_arrive() -> None:
    text = "Good evening, sir. The car is ready! Shall I tell Dr. Smith you're on your way?"
    assert feed_all(text) == [
        "Good evening, sir.",
        "The car is ready!",
        "Shall I tell Dr. Smith you're on your way?",
    ]


def test_chunker_keeps_numbers_and_decimals_together() -> None:
    assert feed_all("It is 3.5 degrees. Version 2.0 is out.") == [
        "It is 3.5 degrees.",
        "Version 2.0 is out.",
    ]


def test_chunker_releases_a_long_first_clause_early() -> None:
    chunker = SentenceChunker(first_min_chars=20)
    first = chunker.feed("Certainly, sir, I have looked into the matter, and the answer is")
    assert first and first[0].endswith(",")


def test_chunker_hard_wraps_runaway_text() -> None:
    pieces = feed_all("word " * 200)
    assert all(len(p) <= 280 for p in pieces) and len(pieces) > 1


def test_clean_for_speech_strips_markdown_and_urls() -> None:
    assert clean_for_speech("**Note:** see [the docs](https://x.y/z) or https://a.b") == (
        "Note: see the docs or"
    )


@respx.mock
async def test_fish_tts_request_shape() -> None:
    route = respx.post("https://api.fish.audio/v1/tts").mock(
        return_value=httpx.Response(200, content=b"ID3mp3bytes")
    )
    tts = FishTTS("sk-fish-test", "voice-123", "s2.1-pro-free")
    speech = await tts.synthesize("Good evening, sir.")
    await tts.aclose()
    assert speech.audio == b"ID3mp3bytes" and speech.media_type == "audio/mpeg"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer sk-fish-test"
    assert req.headers["model"] == "s2.1-pro-free"
    assert json.loads(req.content) == {
        "text": "Good evening, sir.",
        "reference_id": "voice-123",
        "format": "mp3",
    }


@respx.mock
async def test_fish_tts_retries_server_errors_then_succeeds() -> None:
    route = respx.post("https://api.fish.audio/v1/tts").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
    )
    tts = FishTTS("k", "v")
    assert (await tts.synthesize("hi")).audio == b"ok"
    assert route.call_count == 2


@respx.mock
async def test_fish_tts_auth_error_is_not_retried() -> None:
    route = respx.post("https://api.fish.audio/v1/tts").mock(return_value=httpx.Response(401))
    tts = FishTTS("k", "v")
    with pytest.raises(VoiceError) as info:
        await tts.synthesize("hi")
    assert info.value.code == "voice_auth" and route.call_count == 1


@respx.mock
async def test_fish_stt_multipart_and_text() -> None:
    route = respx.post("https://api.fish.audio/v1/asr").mock(
        return_value=httpx.Response(200, json={"text": " Good evening, Alfred. ", "duration": 1.2})
    )
    stt = FishSTT("k")
    assert await stt.transcribe(b"webm-bytes", "audio/webm", "en") == "Good evening, Alfred."
    body = route.calls[0].request.content
    assert b'name="audio"' in body and b"webm-bytes" in body and b'name="language"' in body
