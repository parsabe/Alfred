from __future__ import annotations

import math
from array import array

import pytest

from app.voice.vad import FRAME_BYTES, Segmenter, SpeechEnd, SpeechStart, webrtc_speech_test
from app.voice.wake import find_wake, strip_wake


@pytest.mark.parametrize(
    ("heard", "command"),
    [
        ("Alfred, is the car ready?", "Is the car ready?"),
        ("Alfred is the car ready", "Is the car ready"),
        ("Hey Alfred, what's the time?", "What's the time?"),
        ("OK, Alfred. Turn off the lights.", "Turn off the lights."),
        ("Alfred's the car ready?", "The car ready?"),
        ("Elfred, put the kettle on.", "Put the kettle on."),
        ("Al Fred, good morning.", "Good morning."),
        ("What's the weather tomorrow, Alfred?", "What's the weather tomorrow?"),
        ("Alfred.", ""),
        ("Alfred?", ""),
        ("Hey Alfred", ""),
    ],
)
def test_wake_word_heard(heard: str, command: str) -> None:
    wake = find_wake(heard)
    assert wake.heard and wake.command == command


@pytest.mark.parametrize(
    "heard",
    [
        "The weather is nice today.",
        "I told Alfred about it yesterday and he agreed.",
        "We were offered a deal.",
        "Afraid not.",
        "",
    ],
)
def test_wake_word_not_heard(heard: str) -> None:
    assert not find_wake(heard).heard


def test_strip_wake_for_follow_ups() -> None:
    assert strip_wake("And tomorrow?") == "And tomorrow?"
    assert strip_wake("Alfred, and tomorrow?") == "And tomorrow?"


def test_custom_wake_word() -> None:
    assert find_wake("Jeeves, the door please", name="Jeeves").command == "The door please"


# --- segmentation ----------------------------------------------------------------------------


def tone(seconds: float, amp: int = 8000) -> bytes:
    n = int(16000 * seconds)
    return array(
        "h",
        (
            int(amp * math.sin(2 * math.pi * (140 + 60 * math.sin(i / 900)) * i / 16000))
            for i in range(n)
        ),
    ).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(16000 * seconds) * 2)


def test_segmenter_finds_one_utterance_with_preroll() -> None:
    seg = Segmenter(end_silence_ms=600, preroll_ms=200)
    events = seg.push(silence(0.5) + tone(1.0) + silence(0.8))
    assert isinstance(events[0], SpeechStart)
    ends = [e for e in events if isinstance(e, SpeechEnd)]
    assert len(ends) == 1
    end = ends[0]
    assert 900 <= end.voiced_ms <= 1020
    # pre-roll (0.2 s) + speech (1.0 s) + the silence that ended it (0.6 s)
    assert 1.9 * 16000 * 2 >= len(end.audio) >= 1.6 * 16000 * 2
    assert not seg.active


def test_segmenter_ignores_clicks_and_steady_hum() -> None:
    seg = Segmenter(end_silence_ms=400)
    events = seg.push(silence(0.3) + tone(0.06) + silence(0.6))  # a click
    assert not any(isinstance(e, SpeechEnd) and e.audio for e in events)
    hum = Segmenter()
    # quiet steady background never crosses the adaptive threshold
    assert not hum.push(tone(3.0, amp=150))


def test_segmenter_handles_arbitrary_chunk_sizes() -> None:
    seg = Segmenter(end_silence_ms=400)
    audio = silence(0.3) + tone(0.8) + silence(0.6)
    events = []
    for i in range(0, len(audio), 333):
        events += seg.push(audio[i : i + 333])
    assert sum(isinstance(e, SpeechEnd) and bool(e.audio) for e in events) == 1
    assert len(seg.pending) < FRAME_BYTES


def test_webrtc_test_rejects_silence_and_noise_floor() -> None:
    test = webrtc_speech_test(2)
    frame = silence(0.02)
    assert not test(frame, 0.0, 200.0)
