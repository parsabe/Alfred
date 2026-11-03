"""Offline voice providers for tests and demos: a soft tone per sentence, length ∝ text."""

from __future__ import annotations

import io
import math
import struct
import wave

from app.voice.base import Speech


def tone_wav(seconds: float, freq: float = 196.0, rate: int = 16000) -> bytes:
    frames = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        samples = bytearray()
        for i in range(frames):
            t = i / rate
            # syllable-like amplitude envelope so the ribbon has something to follow
            env = (
                0.5
                * (1 - math.cos(2 * math.pi * min(t / seconds, 1)))
                * (0.55 + 0.45 * abs(math.sin(2 * math.pi * 3.2 * t)))
            )
            samples += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * t)))
        w.writeframes(bytes(samples))
    return buf.getvalue()


class FakeTTS:
    name = "fake"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> Speech:
        self.spoken.append(text)
        return Speech(audio=tone_wav(min(0.06 * len(text), 6.0) + 0.2), format="wav")

    async def aclose(self) -> None:
        return None


class FakeSTT:
    name = "fake"

    def __init__(self, text: str = "Good evening, Alfred.") -> None:
        self.text = text

    async def transcribe(self, audio: bytes, content_type: str, language: str = "") -> str:
        return self.text if audio else ""

    async def aclose(self) -> None:
        return None
