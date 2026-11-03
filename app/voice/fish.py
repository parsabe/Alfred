"""Fish Audio text-to-speech and speech-to-text.

TTS:  POST https://api.fish.audio/v1/tts
      headers: Authorization: Bearer <key>, model: <tts model>
      body:    {"text", "reference_id", "format"}  → audio bytes
STT:  POST https://api.fish.audio/v1/asr  (multipart: audio file [+ language]) → {"text", ...}
"""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.voice.base import Speech, VoiceError

log = logging.getLogger(__name__)

BASE_URL = "https://api.fish.audio"


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, VoiceError) and exc.retryable


def _raise_for(resp: httpx.Response, what: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    code = {401: "voice_auth", 402: "voice_quota", 403: "voice_auth", 429: "voice_rate_limited"}
    raise VoiceError(
        f"Fish Audio {what} failed with HTTP {resp.status_code}",
        code=code.get(resp.status_code, "voice_upstream"),
        retryable=retryable,
    )


class _FishBase:
    def __init__(
        self, api_key: str, *, timeout_s: float = 30.0, client: httpx.AsyncClient | None = None
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, what: str, **kwargs: object) -> httpx.Response:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(multiplier=0.5, max=4),
            reraise=True,
        ):
            with attempt:
                try:
                    resp = await self._client.post(**kwargs)  # type: ignore[arg-type]
                except httpx.TimeoutException as exc:
                    raise VoiceError(
                        "Fish Audio timed out", code="voice_timeout", retryable=True
                    ) from exc
                except httpx.TransportError as exc:
                    raise VoiceError(
                        "could not reach Fish Audio", code="voice_unreachable", retryable=True
                    ) from exc
                _raise_for(resp, what)
                return resp
        raise AssertionError("unreachable")


class FishTTS(_FishBase):
    name = "fish_audio"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "s2.1-pro-free",
        *,
        audio_format: str = "mp3",
        **kwargs: object,
    ) -> None:
        super().__init__(api_key, **kwargs)  # type: ignore[arg-type]
        self.voice_id = voice_id
        self.model = model
        self.audio_format = audio_format

    async def synthesize(self, text: str) -> Speech:
        resp = await self._post(
            "tts",
            url="/v1/tts",
            json={"text": text, "reference_id": self.voice_id, "format": self.audio_format},
            headers={"model": self.model},
        )
        if not resp.content:
            raise VoiceError("Fish Audio returned no audio", code="voice_empty")
        return Speech(audio=resp.content, format=self.audio_format)


class FishSTT(_FishBase):
    name = "fish_audio"

    async def transcribe(self, audio: bytes, content_type: str, language: str = "") -> str:
        data = {"ignore_timestamps": "true"}
        if language:
            data["language"] = language
        resp = await self._post(
            "asr",
            url="/v1/asr",
            files={"audio": ("speech", audio, content_type or "application/octet-stream")},
            data=data,
        )
        try:
            text = resp.json().get("text", "")
        except ValueError as exc:
            raise VoiceError(
                "Fish Audio returned an unreadable transcript", code="voice_upstream"
            ) from exc
        return str(text).strip()
