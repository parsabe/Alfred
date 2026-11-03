"""Run Alfred's interface with no keys: a scripted stand-in brain, temporary database.

    make demo                 # browser voice and browser speech recognition
    make demo ARGS=--tone     # server "voice" is a soft tone (for testing the audio pipeline)

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
from app.voice.fake import FakeSTT, FakeTTS

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tone", action="store_true", help="server TTS plays a tone")
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
        tts=FakeTTS() if args.tone else None,
        stt=FakeSTT(),
    )
    print(f"\n  Alfred demo → http://127.0.0.1:{args.port}   token: {DEMO_TOKEN}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
