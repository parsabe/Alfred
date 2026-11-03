"""Hands-free Alfred: this computer's microphone and speaker, talking to your Alfred server.

Streams the microphone (16 kHz mono PCM) to `WS /v1/voice`; the server listens for "Alfred, …",
answers, and speaks. Ordinary speakers have no echo cancellation, so by default the microphone
is muted while Alfred talks; with a speakerphone/headset, `barge_in` lets you interrupt him.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import ssl
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alfred_desktop.config import Config, ws_url

RATE = 16000
FRAME = 320  # 20 ms


class Rejected(Exception):
    """The server refused the token."""


def decode_wav(data: bytes) -> tuple[Any, int]:
    import numpy as np

    with wave.open(io.BytesIO(data)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        if w.getnchannels() > 1:
            audio = audio.reshape(-1, w.getnchannels())[:, 0]
        return audio, w.getframerate()


class Listener:
    def __init__(
        self,
        cfg: Config,
        *,
        wav: str | None = None,
        save_dir: str = "alfred-replies",
        linger: float = 8.0,
        out: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.wav = wav
        self.save_dir = save_dir
        self.linger = linger
        self.print = out or (lambda s: print(s, end="", flush=True))
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.playing = asyncio.Event()
        self.saved = 0
        self.stop_playback = False

    async def run(self) -> None:
        import websockets

        url = ws_url(self.cfg.server)
        ssl_ctx = ssl.create_default_context() if url.startswith("wss://") else None
        delay = 1.0
        while True:
            try:
                async with websockets.connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {self.cfg.token}"},
                    ssl=ssl_ctx,
                    max_size=16 * 1024 * 1024,
                    open_timeout=15,
                ) as ws:
                    delay = 1.0
                    await self._session(ws)
                    if self.wav:
                        return
            except Rejected:
                raise
            except websockets.ConnectionClosed as exc:
                if exc.rcvd is not None and exc.rcvd.code == 4401:
                    raise Rejected() from exc
                if self.wav:
                    raise
                self.print(f"\n(connection lost; retrying in {delay:.0f}s)\n")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            except Exception as exc:  # network trouble: reconnect quietly
                if self.wav:
                    raise
                self.print(f"\n(disconnected: {type(exc).__name__}; retrying in {delay:.0f}s)\n")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _session(self, ws: Any) -> None:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "mode": "ptt" if self.cfg.push_to_talk else "wake",
                    "sample_rate": RATE,
                    "barge_in": self.cfg.barge_in,
                }
            )
        )
        tasks = [
            asyncio.ensure_future(c)
            for c in (self._capture(ws), self._receive(ws), self._play(ws), self._keys(ws))
        ]
        try:
            await asyncio.wait([tasks[1]], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        tasks[1].result()
        if self.wav:
            self.print(f"\nSaved {self.saved} replies to {self.save_dir}/\n")

    async def _capture(self, ws: Any) -> None:
        if self.wav:
            with wave.open(self.wav) as w:
                if w.getframerate() != RATE or w.getnchannels() != 1:
                    raise SystemExit("the WAV must be 16 kHz mono")
                data = w.readframes(w.getnframes())
            for i in range(0, len(data), FRAME * 2):
                await ws.send(data[i : i + FRAME * 2])
                await asyncio.sleep(0.02)
            await asyncio.sleep(self.linger)
            await ws.close()
            return

        import sounddevice as sd

        loop = asyncio.get_running_loop()
        frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        def on_audio(indata: Any, _frames: int, _time: Any, status: Any) -> None:
            if status:
                print(status, file=sys.stderr)
            chunk = bytes(indata)
            loop.call_soon_threadsafe(lambda: frames.full() or frames.put_nowait(chunk))

        with sd.RawInputStream(
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            device=self.cfg.input_device,
            callback=on_audio,
        ):
            while True:
                chunk = await frames.get()
                if self.playing.is_set() and not self.cfg.barge_in:
                    chunk = bytes(len(chunk))  # stay deaf while he speaks (no echo cancellation)
                await ws.send(chunk)

    async def _receive(self, ws: Any) -> None:
        import websockets

        try:
            async for raw in ws:
                event = json.loads(raw)
                kind, data = event["type"], event.get("data", {})
                if kind == "ready":
                    self.print(
                        "Connected. Press Enter, then speak.\n"
                        if self.cfg.push_to_talk
                        else f"Connected. Say “{data.get('wake_word', 'Alfred')}, …”\n"
                    )
                elif kind == "transcript":
                    self.print(f"\nYou: {data['text']}\nAlfred: ")
                elif kind == "token":
                    self.print(data["text"])
                elif kind == "audio":
                    await self.queue.put(("audio", base64.b64decode(data["data_b64"])))
                elif kind == "done":
                    self.print("\n")
                    await self.queue.put(("done", b""))
                elif kind == "interrupt":
                    self.stop_playback = True
                    while not self.queue.empty():
                        self.queue.get_nowait()
                elif kind == "error":
                    self.print(f"\n[{data.get('code')}] {data.get('message')}\n")
        except websockets.ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == 4401:
                raise Rejected() from exc
            if not self.wav:
                raise

    async def _play(self, ws: Any) -> None:
        while True:
            kind, data = await self.queue.get()
            if kind == "done":
                self.playing.clear()
                await ws.send(json.dumps({"type": "playback", "state": "ended"}))
                continue
            self.playing.set()
            self.stop_playback = False
            if self.wav:  # test mode: save instead of playing
                self.saved += 1
                target = Path(self.save_dir) / f"reply-{self.saved:02d}.wav"
                await asyncio.to_thread(_save, target, data)
                continue
            await asyncio.to_thread(self._play_blocking, data)

    def _play_blocking(self, data: bytes) -> None:
        import sounddevice as sd

        audio, rate = decode_wav(data)
        sd.play(audio, rate, device=self.cfg.output_device)
        while sd.get_stream().active:
            if self.stop_playback:
                sd.stop()
                break
            sd.sleep(30)

    async def _keys(self, ws: Any) -> None:
        if not self.cfg.push_to_talk or self.wav:
            return
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, sys.stdin.readline)
            await ws.send(json.dumps({"type": "wake"}))


def _save(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def listen(cfg: Config, **kwargs: Any) -> int:
    with contextlib.suppress(KeyboardInterrupt):
        try:
            asyncio.run(Listener(cfg, **kwargs).run())
        except Rejected:
            print("\nThe server rejected the access token. Run: alfred setup", file=sys.stderr)
            return 2
    return 0


def list_devices() -> str:
    import sounddevice as sd

    return str(sd.query_devices())
