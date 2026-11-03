"""Fully local voice: faster-whisper (speech → text) and Piper (text → speech).

Models are downloaded once into MODELS_DIR on first start (Whisper from Hugging Face via
faster-whisper, Piper voices from the rhasspy/piper-voices repository), then run offline.
Loading happens in the background (`warmup`), so startup never blocks on a download.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path
from typing import Any

from app.voice.base import Speech, VoiceError

log = logging.getLogger(__name__)

PCM_CONTENT_TYPE = "audio/pcm"  # raw 16-bit little-endian mono, 16 kHz


class _Loader:
    """Loads a heavy model once, in a worker thread, and remembers failures."""

    name = "model"

    def __init__(self) -> None:
        self._model: Any = None
        self._error: str | None = None
        self._task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()

    @property
    def status(self) -> str:
        if self._model is not None:
            return "ready"
        if self._error:
            return "error"
        return "loading" if self._task else "idle"

    @property
    def error(self) -> str | None:
        return self._error

    def _load(self) -> Any:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    async def warmup(self) -> None:
        async with self._lock:
            if self._model is not None or self._error:
                return
            if self._task is None:
                self._task = asyncio.create_task(asyncio.to_thread(self._load))
        try:
            model = await self._task
        except Exception as exc:  # download/load failures must never crash the service
            self._error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("%s failed to load: %s", type(self).__name__, self._error)
            return
        self._model = model

    async def model(self) -> Any:
        await self.warmup()
        if self._model is None:
            raise VoiceError(
                f"{self.name} is not available ({self._error})", code="voice_unavailable"
            )
        return self._model


class WhisperSTT(_Loader):
    name = "whisper"

    def __init__(
        self,
        model: str = "base",
        *,
        models_dir: Path,
        device: str = "auto",
        compute_type: str = "int8",
        beam_size: int = 3,
        language: str = "",
        hint: str = "Alfred",
    ) -> None:
        super().__init__()
        self.model_name = model
        self.models_dir = models_dir
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.language = language
        self.hint = hint
        self._run_lock = asyncio.Lock()

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        self.models_dir.mkdir(parents=True, exist_ok=True)
        log.info("loading Whisper %s (first run downloads it)…", self.model_name)
        return WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            download_root=str(self.models_dir / "whisper"),
        )

    async def transcribe(self, audio: bytes, content_type: str, language: str = "") -> str:
        model = await self.model()
        import numpy as np

        if content_type.startswith(PCM_CONTENT_TYPE):
            samples: Any = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
        else:
            from faster_whisper import decode_audio

            samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)

        def run() -> str:
            segments, _info = model.transcribe(
                samples,
                language=(language or self.language or None),
                beam_size=self.beam_size,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=self.hint,  # biases recognition towards the wake word's spelling
            )
            return " ".join(s.text.strip() for s in segments).strip()

        async with self._run_lock:  # one transcription at a time; the model isn't re-entrant
            return await asyncio.to_thread(run)

    async def aclose(self) -> None:
        self._model = None


class PiperTTS(_Loader):
    name = "piper"

    def __init__(self, voice: str, *, models_dir: Path, speed: float = 1.0) -> None:
        super().__init__()
        self.voice = voice
        self.models_dir = models_dir
        self.speed = speed
        self._run_lock = asyncio.Lock()

    def _load(self) -> Any:
        from piper import PiperVoice
        from piper.download_voices import download_voice

        voices = self.models_dir / "piper"
        voices.mkdir(parents=True, exist_ok=True)
        onnx = voices / f"{self.voice}.onnx"
        if not onnx.exists():
            log.info("downloading Piper voice %s…", self.voice)
            download_voice(self.voice, voices)
        return PiperVoice.load(onnx)

    async def synthesize(self, text: str) -> Speech:
        voice = await self.model()
        from piper import SynthesisConfig

        def run() -> bytes:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                voice.synthesize_wav(
                    text, wav, syn_config=SynthesisConfig(length_scale=1 / self.speed)
                )
            return buf.getvalue()

        async with self._run_lock:
            audio = await asyncio.to_thread(run)
        return Speech(audio=audio, format="wav")

    async def aclose(self) -> None:
        self._model = None
