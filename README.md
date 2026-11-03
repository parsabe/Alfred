# Alfred

A discreet, highly capable personal assistant you talk to. Open it in a browser, tap anywhere,
speak; Alfred answers aloud in his own voice while the ribbon moves with whoever is talking.
The backend is a plain HTTP/SSE service, so any other interface (CLI, phone, voice device) can use it too.

![The ribbon](docs/interface-reference.png)

- Requirements and progress: [`docs/BRIEF.md`](docs/BRIEF.md) (Status at the bottom)
- Design: [`docs/PLAN.md`](docs/PLAN.md)
- API contract with curl examples: [`docs/API.md`](docs/API.md)

## Quick start

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/parsabe/alfred && cd alfred
make install
cp .env.example .env
make token          # paste the output into ALFRED_API_TOKEN in .env
```

Choose a model in `.env`:

- **Anthropic (recommended):** set `ANTHROPIC_API_KEY`. Default model `claude-opus-5-5`.
- **Gemma, fully local:** install [Ollama](https://ollama.com), run `ollama pull gemma3:12b`, and
  keep `LOCAL_LLM_BASE_URL=http://localhost:11434/v1`. With no Anthropic key, Alfred uses Gemma
  automatically; with one, Gemma becomes the fallback.

```bash
make dev            # http://127.0.0.1:8000  (docs at /docs)
```

Open **http://127.0.0.1:8000**, enter your token (gear icon), then tap anywhere and speak.

| | |
|---|---|
| tap / Space | talk; tap again to stop. Tapping while Alfred speaks interrupts him. |
| `/` | type instead of speaking |
| `,` | settings: token, conversation mode, voice, captions, recognition engine, language |
| Esc | stop everything |

In **conversation mode** (on by default) Alfred listens again as soon as he finishes answering,
so you can just keep talking.

Alfred's voice comes from Fish Audio (`FISH_AUDIO_API_KEY`). Without it, the browser's built-in
voice stands in. Speech recognition uses the browser's own (Chrome, Edge, Safari) or, in
Firefox or if you choose it, Fish Audio on the server.

**No keys yet?** `make demo` runs the interface with a stand-in brain so you can try it
(token `demo-token-not-secret-0123456789abcdef`).

Or from the command line:

```bash
curl -sN http://127.0.0.1:8000/v1/chat \
  -H "Authorization: Bearer <your token>" -H "Content-Type: application/json" \
  -d '{"message": "Good evening, Alfred."}'
```

Edit Alfred's persona in `prompts/alfred_system.md`. Changes apply on the next message, without a restart.

## Docker

```bash
make docker-up                                   # Alfred on 127.0.0.1:8000, data in a volume
docker compose --profile local-llm up -d ollama  # optional: Gemma in a container
docker compose exec ollama ollama pull gemma3:12b
```

## Development

```bash
make test        # pytest
make check       # ruff + mypy + pytest (what CI runs)
make fmt         # format and autofix
make e2e         # drive the voice interface in headless Chromium (Node + Playwright)
```
