"""Voice provider protocols (text-to-speech, speech-to-text)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class VoiceError(Exception):
    def __init__(self, message: str, *, code: str = "voice_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class Speech:
    audio: bytes
    format: str  # "mp3" | "wav" | "opus"

    @property
    def media_type(self) -> str:
        return {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg"}.get(
            self.format, "application/octet-stream"
        )


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str) -> Speech: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, content_type: str, language: str = "") -> str: ...

    async def aclose(self) -> None: ...
