# Alfred

A discreet, highly capable personal assistant, built as a backend service that any interface can
talk to: the ribbon UI, a CLI, a phone, a voice device.

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

Say hello:

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
```
