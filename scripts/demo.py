"""Try Alfred's interface without Ollama: a stand-in brain and a temporary database.

    make demo                       # real local hearing (Whisper) and voice (Piper)
    make demo ARGS="--fake-voice"   # no models at all: scripted hearing, tones for a voice

Open http://127.0.0.1:8000 and enter the token printed below.
"""

from __future__ import annotations

import argparse
import itertools
import tempfile
from pathlib import Path

import uvicorn

from app.config import Settings
from app.llm.base import LLMRequest
from app.llm.fake import FakeProvider, Script
from app.main import create_app
from app.voice.fake import FakeTTS

DEMO_TOKEN = "demo-token-not-secret-0123456789abcdef"  # noqa: S105 (public demo value)

_LINES = itertools.cycle(
    [
        "Very good, sir. I heard: {said}. I'm running in demo mode, so my wits are on loan "
        "until a language model is connected.",
        "Noted, sir. {said}. Once you give me a proper mind, I'll have a proper answer.",
        "Of course, sir. You said {said}. The interface is working; the brain is a stand-in.",
    ]
)


def respond(request: LLMRequest) -> Script:
    said = next((m.text for m in reversed(request.messages) if m.role == "user"), "")
    return Script(text=next(_LINES).format(said=said.strip().rstrip(".?!")), delay_s=0.6)


class ScriptedSTT:
    """Pretends to hear the given lines, one per utterance, then silence."""

    name = "scripted"

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    async def transcribe(self, audio: bytes, content_type: str, language: str = "") -> str:
        return self.lines.pop(0) if self.lines else ""

    async def aclose(self) -> None:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-voice", action="store_true", help="no speech models needed")
    parser.add_argument(
        "--script",
        default="Alfred, is the car ready?|Thank you, Alfred.",
        help="with --fake-voice: what Alfred 'hears', one utterance per |",
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    db = Path(tempfile.mkdtemp(prefix="alfred-demo-")) / "demo.db"
    settings = Settings(
        _env_file=None,
        alfred_api_token=DEMO_TOKEN,
        database_url=f"sqlite+aiosqlite:///{db}",
        llm_provider="fake",
        owner_address_as="sir",
    )
    app = create_app(
        settings,
        llm=FakeProvider(responder=respond),
        tts=FakeTTS() if args.fake_voice else None,
        stt=ScriptedSTT(args.script.split("|")) if args.fake_voice else None,
    )
    print(f"\n  Alfred demo → http://127.0.0.1:{args.port}   token: {DEMO_TOKEN}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
