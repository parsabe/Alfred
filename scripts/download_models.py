"""Download and load Alfred's local speech models now, so the first "Alfred, …" is instant.

    uv run python -m scripts.download_models

Uses the settings from .env (WHISPER_MODEL, PIPER_VOICE, MODELS_DIR).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from app.config import get_settings
from app.main import build_voice


async def main() -> int:
    settings = get_settings()
    tts, stt = build_voice(settings)
    ok = True
    providers: tuple[tuple[str, Any], ...] = (("hearing (Whisper)", stt), ("voice (Piper)", tts))
    for label, provider in providers:
        if provider is None or not hasattr(provider, "warmup"):
            print(f"  {label}: not using a local model, skipped")
            continue
        print(f"  {label}: downloading/loading…", flush=True)
        await provider.warmup()
        status = provider.status
        print(f"  {label}: {status}" + (f" ({provider.error})" if status == "error" else ""))
        ok &= status == "ready"
    if ok and tts is not None:
        speech = await tts.synthesize("Good evening, sir. Everything is in order.")
        print(f"  voice test: {len(speech.audio) // 1024} KB of audio synthesized")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
