"""Hands-free voice conversation over a continuous microphone stream.

    idle (asleep) ──"Alfred, …"──▶ thinking ──▶ speaking ──playback ends──▶ listening
         ▲                                         │  (you talk)                 │
         └──────── follow-up window expires ───────┼── interrupt ◀───────────────┘
                                                   ▼
                                               listening

- asleep: every utterance is transcribed locally; only ones addressed to Alfred are acted on,
  the rest are dropped immediately (never stored, never sent anywhere).
- "Alfred" alone → "Yes, sir?" and he listens for the next sentence.
- after he answers, follow-ups need no wake word for FOLLOW_UP_WINDOW_S seconds.
- barge-in: talking while he speaks first lowers his voice (duck), then interrupts once it is
  clearly speech addressed to him and not his own voice echoing back.

Interface-agnostic: feed PCM with `feed_audio`, control messages with `handle_control`, read
events from `events`. The WebSocket endpoint and any other transport are thin adapters.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
import re
import time
import wave
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

from app.config import Settings
from app.core import events as ev
from app.core.agent import Agent
from app.core.events import AgentState, Event
from app.core.voice_turn import speak_turn
from app.llm.base import ChatMessage
from app.voice.base import STTProvider, TTSProvider, VoiceError
from app.voice.vad import (
    Segmenter,
    SpeechEnd,
    SpeechProgress,
    SpeechStart,
    SpeechTest,
    webrtc_speech_test,
)
from app.voice.wake import find_wake, strip_wake

log = logging.getLogger(__name__)

Mode = Literal["wake", "ptt", "always"]
PCM_TYPE = "audio/pcm;rate=16000"
_WORDS = re.compile(r"[\w']+")


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def audio_seconds(data_b64: str, fmt: str, text: str) -> float:
    if fmt == "wav":
        with contextlib.suppress(Exception), wave.open(io.BytesIO(base64.b64decode(data_b64))) as w:
            return float(w.getnframes()) / float(w.getframerate())
    return 0.075 * len(text) + 0.3


class VoiceSession:
    def __init__(
        self,
        *,
        agent: Agent,
        settings: Settings,
        stt: STTProvider | None,
        tts: TTSProvider | None,
        mode: Mode = "wake",
        conversation_id: str | None = None,
        barge_in: bool | None = None,
        speech_test: SpeechTest | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.agent = agent
        self.settings = settings
        self.stt = stt
        self.tts = tts
        self.mode: Mode = mode
        # per-device override: a speaker without echo cancellation would interrupt itself
        self.barge_in = settings.barge_in if barge_in is None else barge_in
        self.conversation_id = conversation_id
        self.clock = clock
        self.events: asyncio.Queue[Event] = asyncio.Queue()
        self.state: AgentState = "idle"
        self.segmenter = Segmenter(
            speech_test or webrtc_speech_test(settings.vad_aggressiveness),
            end_silence_ms=settings.end_of_speech_ms,
        )
        self._seq = 0
        self._utterances: asyncio.Queue[tuple[bytes, int, AgentState]] = asyncio.Queue()
        self._worker = asyncio.create_task(self._process_utterances())
        self._turn: asyncio.Task[None] | None = None
        self._window: asyncio.TimerHandle | None = None
        self._playback_guard: asyncio.TimerHandle | None = None
        self._awaiting_playback = False
        self._speech_began_in: AgentState = "idle"
        self._ducked = False
        self._interrupted_by_this_speech = False
        self._spoken: deque[str] = deque(maxlen=8)  # Alfred's recent sentences (echo check)
        self._ack_cache: dict[str, Event] = {}
        self._stt_error_sent = False
        self._background: set[asyncio.Task[Any]] = set()

    # --- output ----------------------------------------------------------------------------

    def emit(self, event: Event) -> None:
        self._seq += 1
        event.seq = self._seq
        event.conversation_id = event.conversation_id or self.conversation_id
        self.events.put_nowait(event)

    def set_state(self, state: AgentState) -> None:
        if state != self.state:
            self.state = state
            self.emit(ev.state(state))

    def ready(self) -> None:
        self.emit(
            Event(
                type="ready",
                data={
                    "mode": self.mode,
                    "wake_word": self.settings.wake_word,
                    "follow_up_window_s": self.settings.follow_up_window_s,
                    "barge_in": self.barge_in,
                    "stt": getattr(self.stt, "name", None),
                    "tts": getattr(self.tts, "name", None),
                    "sample_rate": 16000,
                },
            )
        )
        self.emit(ev.state(self.state))

    # --- input -----------------------------------------------------------------------------

    async def feed_audio(self, pcm: bytes) -> None:
        if self.mode == "ptt" and self.state == "idle":
            self.segmenter.reset()  # push-to-talk: nothing is heard until you tap
            return
        for item in self.segmenter.push(pcm):
            if isinstance(item, SpeechStart):
                self._speech_began_in = self.state
                self._interrupted_by_this_speech = False
                self.emit(Event(type="user_speech", data={"value": "start"}))
            elif isinstance(item, SpeechProgress):
                self._maybe_barge_in(item.voiced_ms)
            elif isinstance(item, SpeechEnd):
                self.emit(Event(type="user_speech", data={"value": "end"}))
                if item.audio:
                    await self._utterances.put((item.audio, item.voiced_ms, self._speech_began_in))
                elif self._ducked and not self._interrupted_by_this_speech:
                    self._unduck()

    async def handle_control(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "wake":  # tap / button: listen without the wake word
            if self.state in ("thinking", "speaking"):
                self.interrupt("tap")
            else:
                self.set_state("listening")
                self._start_window(max(self.settings.follow_up_window_s, 8.0))
        elif kind in ("interrupt", "stop"):
            self.interrupt("client")
            if kind == "stop":
                self._sleep()
        elif kind == "sleep":
            self._sleep()
        elif kind == "text":
            text = str(message.get("message", "")).strip()[:20000]
            if text:
                if self.state in ("thinking", "speaking"):
                    self.interrupt("text")
                self._start_turn(text, text)
        elif kind == "playback" and message.get("state") == "ended":
            self._playback_finished()
        elif kind == "mode" and message.get("mode") in ("wake", "ptt", "always"):
            self.mode = message["mode"]

    # --- utterances --------------------------------------------------------------------------

    async def _process_utterances(self) -> None:
        while True:
            audio, voiced_ms, began_in = await self._utterances.get()
            if self.stt is None:
                self._stt_problem("Speech recognition isn't configured on the server.")
                continue
            try:
                text = await self.stt.transcribe(audio, PCM_TYPE, self.settings.speech_language)
            except VoiceError as exc:
                self._stt_problem(str(exc))
                if self._ducked:
                    self._unduck()
                continue
            except Exception:
                log.exception("transcription failed")
                continue
            try:
                await self._on_transcript(text, voiced_ms, began_in)
            except Exception:
                log.exception("handling transcript failed")

    def _stt_problem(self, message: str) -> None:
        if not self._stt_error_sent:
            self._stt_error_sent = True
            self.emit(ev.error("stt_unavailable", message))

    async def _on_transcript(self, text: str, voiced_ms: int, began_in: AgentState) -> None:
        text = text.strip()
        name = self.settings.wake_word
        if not _tokens(text) or (_tokens(text) == ["you"] and voiced_ms < 600):
            if self._ducked:
                self._unduck()
            return  # silence, a cough, or a typical recogniser phantom

        busy = began_in in ("thinking", "speaking") or self.state in ("thinking", "speaking")
        if busy:
            if not self._interrupted_by_this_speech and self._is_echo(text):
                self._unduck()
                return
            if not self._interrupted_by_this_speech:
                self.interrupt("speech")
            command = strip_wake(text, name)
        elif self.state == "listening" or self.mode == "always":
            command = strip_wake(text, name)
        else:  # asleep: only act when addressed
            wake = find_wake(text, name)
            if not wake.heard:
                return
            command = wake.command

        if command:
            self._start_turn(command, text)
        else:
            await self._acknowledge(text)

    def _is_echo(self, text: str) -> bool:
        heard = _tokens(text)
        spoken = set(_tokens(" ".join(self._spoken)))
        if len(heard) < 2 or not spoken:
            return False
        overlap = sum(1 for w in heard if w in spoken) / len(heard)
        return overlap >= 0.7

    # --- barge-in ----------------------------------------------------------------------------

    def _maybe_barge_in(self, voiced_ms: int) -> None:
        if not self.barge_in or self.state not in ("speaking", "thinking"):
            return
        if voiced_ms >= self.settings.barge_in_ms and not self._ducked:
            self._ducked = True
            self.emit(Event(type="duck", data={}))
        if voiced_ms >= self.settings.barge_in_hard_ms and not self._interrupted_by_this_speech:
            self.interrupt("speech")

    def _unduck(self) -> None:
        if self._ducked:
            self._ducked = False
            self.emit(Event(type="unduck", data={}))

    def interrupt(self, reason: str) -> None:
        busy = self.state in ("thinking", "speaking") or (self._turn and not self._turn.done())
        if self._turn and not self._turn.done():
            self._turn.cancel()  # mid-generation: the agent records the cut-off reply itself
        elif self._awaiting_playback and self.conversation_id:
            # the reply was complete but you stopped him mid-sentence: let him know
            self._note(
                "The owner interrupted Alfred while he was still speaking that reply, so may "
                "not have heard all of it."
            )
        self._turn = None
        self._awaiting_playback = False
        self._cancel(self._playback_guard)
        self._ducked = False
        if reason == "speech":
            self._interrupted_by_this_speech = True
        if busy:
            self.emit(Event(type="interrupt", data={"reason": reason}))
        self.set_state("listening")
        self._start_window(max(self.settings.follow_up_window_s, 8.0))

    def _note(self, text: str) -> None:
        conversation_id = self.conversation_id
        if conversation_id is None:
            return
        store = self.agent.store
        task = asyncio.create_task(
            store.append(conversation_id, ChatMessage(role="context", text=text))
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- turns ---------------------------------------------------------------------------------

    def _start_turn(self, command: str, heard: str) -> None:
        self._cancel(self._window)
        self.set_state("thinking")
        self._turn = asyncio.create_task(self._run_turn(command, heard))

    async def _run_turn(self, command: str, heard: str) -> None:
        if self.conversation_id is None:
            # create it up front so an interrupted first turn and its follow-up stay together
            self.conversation_id = (await self.agent.store.create(title=command[:60])).id
        self.emit(Event(type="transcript", data={"text": heard, "command": command}))
        stream = self.agent.run_turn(command, self.conversation_id, "voice")
        if self.tts is not None:
            stream = speak_turn(stream, self.tts)
        spoken_s = 0.0
        try:
            async for event in stream:
                if event.type == "state":
                    continue  # the session owns the state machine
                if event.conversation_id:
                    self.conversation_id = event.conversation_id
                if event.type == "audio":
                    self._spoken.append(event.data.get("text", ""))
                    spoken_s += audio_seconds(
                        event.data["data_b64"],
                        event.data.get("format", ""),
                        event.data.get("text", ""),
                    )
                    self.set_state("speaking")
                self.emit(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("voice turn failed")
            self.emit(ev.error("turn_failed", "Something went wrong; please say that again."))
        if spoken_s > 0:
            self._await_playback(spoken_s)
        else:
            self._reply_finished()

    async def _acknowledge(self, heard: str) -> None:
        address = self.settings.owner_address_as or "sir"
        phrase = f"Yes, {address}?"
        self._cancel(self._window)
        self.emit(Event(type="transcript", data={"text": heard, "command": ""}))
        audio = self._ack_cache.get(phrase)
        if audio is None and self.tts is not None:
            try:
                speech = await self.tts.synthesize(phrase)
                audio = Event(
                    type="audio",
                    data={
                        "index": 1,
                        "format": speech.format,
                        "media_type": speech.media_type,
                        "text": phrase,
                        "data_b64": base64.b64encode(speech.audio).decode(),
                    },
                )
                self._ack_cache[phrase] = audio
            except VoiceError:
                audio = None
        self.emit(ev.token(phrase))
        if audio is not None:
            self.set_state("speaking")
            self.emit(audio.model_copy(deep=True))
            self.emit(
                ev.done(status="ack", usage={}, cost_usd=0, model="", provider="", message_id=None)
            )
            self._await_playback(
                audio_seconds(audio.data["data_b64"], audio.data["format"], phrase)
            )
        else:
            self.emit(
                ev.done(status="ack", usage={}, cost_usd=0, model="", provider="", message_id=None)
            )
            self._reply_finished()

    def _await_playback(self, seconds: float) -> None:
        """Wait for the client to say playback ended; fall back to the audio's own length."""
        self._awaiting_playback = True
        self._cancel(self._playback_guard)
        loop = asyncio.get_running_loop()
        self._playback_guard = loop.call_later(seconds + 4.0, self._playback_finished)

    def _playback_finished(self) -> None:
        if not self._awaiting_playback:
            return
        self._awaiting_playback = False
        self._cancel(self._playback_guard)
        self._reply_finished()

    def _reply_finished(self) -> None:
        self._unduck()
        if self.mode == "ptt" or self.settings.follow_up_window_s <= 0:
            self._sleep()
            return
        self.set_state("listening")
        self._start_window(self.settings.follow_up_window_s)

    # --- follow-up window -------------------------------------------------------------------

    def _start_window(self, seconds: float) -> None:
        self._cancel(self._window)
        self._window = asyncio.get_running_loop().call_later(seconds, self._window_expired)

    def _window_expired(self) -> None:
        if self.state != "listening":
            return
        if self.segmenter.active or not self._utterances.empty():
            self._start_window(2.0)  # you're mid-sentence: keep listening
            return
        self._sleep()

    def _sleep(self) -> None:
        self._cancel(self._window)
        if self.state != "idle":
            self.set_state("idle")

    @staticmethod
    def _cancel(handle: asyncio.TimerHandle | None) -> None:
        if handle is not None:
            handle.cancel()

    async def close(self) -> None:
        self._cancel(self._window)
        self._cancel(self._playback_guard)
        for task in (self._turn, self._worker):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
