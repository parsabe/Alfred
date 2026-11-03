"""Voice activity detection and utterance segmentation for a continuous microphone stream.

Input: 16 kHz mono 16-bit PCM in any chunk size. Output: speech start / progress / end events,
the end event carrying the utterance audio (with a little audio from just before speech began,
so the first syllable isn't clipped).

A frame counts as speech when WebRTC's VAD says so *and* it is clearly louder than the room's
background (tracked continuously), which rejects fans, hum and steady noise.
"""

from __future__ import annotations

import math
from array import array
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # 640


@dataclass
class SpeechStart:
    pass


@dataclass
class SpeechProgress:
    voiced_ms: int


@dataclass
class SpeechEnd:
    audio: bytes
    voiced_ms: int


SegmentEvent = SpeechStart | SpeechProgress | SpeechEnd
SpeechTest = Callable[[bytes, float, float], bool]  # (frame, rms, noise_floor) -> is speech


def frame_rms(frame: bytes) -> float:
    samples = array("h", frame)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def webrtc_speech_test(aggressiveness: int = 2) -> SpeechTest:
    import webrtcvad

    vad = webrtcvad.Vad(aggressiveness)

    def test(frame: bytes, rms: float, floor: float) -> bool:
        return rms > max(250.0, floor * 2.5) and vad.is_speech(frame, SAMPLE_RATE)

    return test


def energy_speech_test(frame: bytes, rms: float, floor: float) -> bool:
    return rms > max(250.0, floor * 3.0)


class Segmenter:
    def __init__(
        self,
        is_speech: SpeechTest = energy_speech_test,
        *,
        start_ms: int = 120,
        end_silence_ms: int = 700,
        min_speech_ms: int = 200,
        max_utterance_ms: int = 30000,
        preroll_ms: int = 300,
    ) -> None:
        self.is_speech = is_speech
        self.start_frames = max(1, start_ms // FRAME_MS)
        self.end_frames = max(1, end_silence_ms // FRAME_MS)
        self.min_frames = max(1, min_speech_ms // FRAME_MS)
        self.max_frames = max_utterance_ms // FRAME_MS
        self.preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self.pending = b""
        self.floor = 200.0
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.frames: list[bytes] = []
        self.voiced = 0
        self.run = 0  # consecutive voiced frames before start
        self.silence = 0

    def push(self, pcm: bytes) -> list[SegmentEvent]:
        self.pending += pcm
        events: list[SegmentEvent] = []
        while len(self.pending) >= FRAME_BYTES:
            frame, self.pending = self.pending[:FRAME_BYTES], self.pending[FRAME_BYTES:]
            events += self._frame(frame)
        return events

    def _frame(self, frame: bytes) -> list[SegmentEvent]:
        rms = frame_rms(frame)
        speech = self.is_speech(frame, rms, self.floor)
        if not speech:
            # background level adapts slowly upwards, quickly downwards
            rate = 0.02 if rms > self.floor else 0.2
            self.floor += (rms - self.floor) * rate
        if not self.active:
            self.preroll.append(frame)
            self.run = self.run + 1 if speech else 0
            if self.run >= self.start_frames:
                self.active = True
                self.frames = list(self.preroll)
                self.voiced = self.run
                self.silence = 0
                return [SpeechStart()]
            return []

        self.frames.append(frame)
        if speech:
            self.voiced += 1
            self.silence = 0
        else:
            self.silence += 1
        out: list[SegmentEvent] = []
        if speech and self.voiced % 5 == 0:
            out.append(SpeechProgress(self.voiced * FRAME_MS))
        if self.silence >= self.end_frames or len(self.frames) >= self.max_frames:
            audio, voiced = b"".join(self.frames), self.voiced
            self.preroll.clear()
            self.reset()
            if voiced >= self.min_frames:
                out.append(SpeechEnd(audio, voiced * FRAME_MS))
            else:
                out.append(SpeechEnd(b"", voiced * FRAME_MS))  # a click or a cough
        return out
