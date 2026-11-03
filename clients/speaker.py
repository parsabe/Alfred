#!/usr/bin/env python3
"""Alfred as a hands-free speaker: microphone and loudspeaker, no screen.

Runs on a Raspberry Pi, an old laptop, a desktop, any computer with Python 3.9+, a microphone
and a speaker. Connects to your Alfred server, listens for "Alfred, …", and speaks his answers.

    pip install websockets sounddevice numpy
    python speaker.py --server https://alfred.your-tailnet.ts.net --token <ALFRED_API_TOKEN>

Options:
    --push-to-talk    only listen after you press Enter (no wake word)
    --barge-in        let your voice interrupt him (needs a speakerphone/headset with echo
                      cancellation; otherwise he would hear himself and stop talking)
    --input-device N / --output-device N   pick devices (list them with --list-devices)
    --wav FILE        test mode: stream a 16 kHz mono WAV as the microphone, save replies as WAVs

This file has no dependency on the server code; copy it anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import os
import ssl
import sys
import wave
from pathlib import Path

RATE = 16000
FRAME = 320  # 20 ms


def ws_url(server: str) -> str:
    server = server.rstrip("/")
    return server.replace("https://", "wss://").replace("http://", "ws://") + "/v1/voice"


def decode_wav(data: bytes):  # -> (numpy int16 array, sample rate)
    import numpy as np

    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(w.getnframes())
        audio = np.frombuffer(frames, dtype="<i2")
        if w.getnchannels() > 1:
            audio = audio.reshape(-1, w.getnchannels())[:, 0]
        return audio, w.getframerate()


class Speaker:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.playing = asyncio.Event()
        self.turn_done = False
        self.saved = 0
        self.stop_playback = False

    async def run(self) -> None:
        import websockets

        ssl_ctx = ssl.create_default_context() if self.args.server.startswith("https") else None
        headers = {"Authorization": f"Bearer {self.args.token}"}
        delay = 1.0
        while True:
            try:
                async with websockets.connect(
                    ws_url(self.args.server),
                    additional_headers=headers,
                    ssl=ssl_ctx,
                    max_size=16 * 1024 * 1024,
                ) as ws:
                    delay = 1.0
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "mode": "ptt" if self.args.push_to_talk else "wake",
                                "sample_rate": RATE,
                                "barge_in": self.args.barge_in,
                            }
                        )
                    )
                    print(
                        "Connected. Say “Alfred, …”"
                        if not self.args.push_to_talk
                        else "Connected. Press Enter, then speak.",
                        flush=True,
                    )
                    tasks = [
                        asyncio.ensure_future(c)
                        for c in (self.capture(ws), self.receive(ws), self.play(ws), self.keys(ws))
                    ]
                    try:
                        # the connection is over as soon as the server side ends or fails
                        await asyncio.wait(tasks[1:2], return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    tasks[1].result()  # re-raise a receive failure to trigger reconnect
                    if self.args.wav:
                        print(f"\nSaved {self.saved} replies to {self.args.save_dir}/", flush=True)
                        return
                    raise ConnectionError("connection closed")
            except (OSError, Exception) as exc:  # reconnect on any network failure
                if self.args.wav:
                    raise
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                if code == 4401:
                    sys.exit("The server rejected the token.")
                print(f"Disconnected ({type(exc).__name__}); retrying in {delay:.0f}s", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def capture(self, ws) -> None:
        if self.args.wav:
            with wave.open(self.args.wav) as w:
                assert w.getframerate() == RATE and w.getnchannels() == 1, "WAV must be 16 kHz mono"
                data = w.readframes(w.getnframes())
            for i in range(0, len(data), FRAME * 2):
                await ws.send(data[i : i + FRAME * 2])
                await asyncio.sleep(0.02)
            await asyncio.sleep(self.args.linger)
            await ws.close()
            return

        import sounddevice as sd

        loop = asyncio.get_running_loop()
        frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        def callback(indata, _frames, _time, status) -> None:
            if status:
                print(status, file=sys.stderr)
            data = bytes(indata)
            loop.call_soon_threadsafe(lambda: frames.full() or frames.put_nowait(data))

        with sd.RawInputStream(
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            device=self.args.input_device,
            callback=callback,
        ):
            while True:
                data = await frames.get()
                if self.playing.is_set() and not self.args.barge_in:
                    data = bytes(len(data))  # no echo cancellation: stay deaf while he speaks
                await ws.send(data)

    async def receive(self, ws) -> None:
        async for raw in ws:
            event = json.loads(raw)
            kind, data = event["type"], event.get("data", {})
            if kind == "transcript":
                print(f"\nYou: {data['text']}", flush=True)
                print("Alfred: ", end="", flush=True)
            elif kind == "token":
                print(data["text"], end="", flush=True)
            elif kind == "audio":
                await self.queue.put(("audio", base64.b64decode(data["data_b64"])))
            elif kind == "done":
                print(flush=True)
                await self.queue.put(("done", b""))
            elif kind == "interrupt":
                self.stop_playback = True
                while not self.queue.empty():
                    self.queue.get_nowait()
            elif kind == "error":
                print(f"\n[{data.get('code')}] {data.get('message')}", flush=True)
            elif kind == "state" and data.get("value") == "listening" and self.args.push_to_talk:
                print("(listening…)", flush=True)

    async def play(self, ws) -> None:
        while True:
            kind, data = await self.queue.get()
            if kind == "done":
                self.playing.clear()
                await ws.send(json.dumps({"type": "playback", "state": "ended"}))
                continue
            self.playing.set()
            self.stop_playback = False
            if self.args.wav:  # test mode: save instead of playing
                self.saved += 1
                target = Path(self.args.save_dir) / f"reply-{self.saved:02d}.wav"
                await asyncio.to_thread(self._save, target, data)
                continue
            await asyncio.to_thread(self._play_blocking, data)

    @staticmethod
    def _save(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def _play_blocking(self, data: bytes) -> None:
        import sounddevice as sd

        audio, rate = decode_wav(data)
        sd.play(audio, rate, device=self.args.output_device)
        while sd.get_stream().active:
            if self.stop_playback:
                sd.stop()
                break
            sd.sleep(30)

    async def keys(self, ws) -> None:
        if not self.args.push_to_talk or self.args.wav:
            return
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, sys.stdin.readline)
            await ws.send(json.dumps({"type": "wake"}))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--server", default=os.environ.get("ALFRED_SERVER", "http://127.0.0.1:8000")
    )
    parser.add_argument("--token", default=os.environ.get("ALFRED_TOKEN", ""))
    parser.add_argument("--push-to-talk", action="store_true")
    parser.add_argument("--barge-in", action="store_true")
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--wav", help=argparse.SUPPRESS)
    parser.add_argument("--save-dir", default="alfred-replies", help=argparse.SUPPRESS)
    parser.add_argument("--linger", type=float, default=8.0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    if not args.token:
        sys.exit("Give the access token with --token or ALFRED_TOKEN.")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Speaker(args).run())


if __name__ == "__main__":
    main()
