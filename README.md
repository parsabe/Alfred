# Alfred · kernel

The server: Alfred's mind, ears and voice, and the API every device talks to.
**Gemma 3 12B** (Ollama) thinks, **Whisper** hears, **Piper** speaks, all on your own machine,
with no API keys and no cloud services.

> This is the `kernel` branch of [parsabe/Alfred](https://github.com/parsabe/Alfred).
> Other parts live on their own branches: [`web`](https://github.com/parsabe/Alfred/tree/web)
> (interface), [`android`](https://github.com/parsabe/Alfred/tree/android),
> [`linux`](https://github.com/parsabe/Alfred/tree/linux),
> [`windows`](https://github.com/parsabe/Alfred/tree/windows),
> [`docs`](https://github.com/parsabe/Alfred/tree/docs).

## Install on an Ubuntu server

```bash
git clone --branch kernel https://github.com/parsabe/Alfred.git && cd Alfred
sudo ./deploy/install.sh --tailscale
```

Installs Ollama + Gemma 3 12B, fetches the web interface from the `web` branch, downloads
Whisper and Piper, runs Alfred as a systemd service, and gives him a private `https://` address
via Tailscale. Prints the address and your access token. Options: `--lan`,
`ALFRED_MODEL=gemma3:4b` (faster on CPU-only servers). Connecting phones, watches and computers:
[DEVICES.md](https://github.com/parsabe/Alfred/blob/docs/DEVICES.md).

## What's here

| Path | |
|---|---|
| `app/core/` | agent turn, hands-free voice session (wake word, barge-in), persona, context |
| `app/llm/` | Ollama/OpenAI-compatible provider (Gemma), plus optional others, with failover |
| `app/voice/` | Whisper + Piper (local), speech detection, wake word, sentence chunking |
| `app/api/` | HTTP/SSE/WebSocket routes: `/v1/chat`, `/v1/ask`, `WS /v1/voice`, `/v1/tts`, `/v1/stt`, conversations, health |
| `app/security/` | token auth, IP allowlist, log redaction |
| `prompts/alfred_system.md` | Alfred's personality (edits apply immediately) |
| `deploy/` | Ubuntu installer and systemd unit |
| `scripts/` | demo server, model download, web fetch |
| `tests/` | unit + integration tests, headless-browser e2e |

API reference: [API.md](https://github.com/parsabe/Alfred/blob/docs/API.md).

## Development

Python 3.12+ and [uv](https://docs.astral.sh/uv/); for a real brain, Ollama with `ollama pull gemma3:12b`.

```bash
make install                          # dependencies + git hooks
cp .env.example .env && make token    # put the token into ALFRED_API_TOKEN
make web                              # fetch the interface from the web branch
make models                           # download Whisper + Piper once
make dev                              # http://127.0.0.1:8000
make demo                             # stand-in brain, no Ollama needed
make check                            # lint + types + tests
make e2e                              # hands-free flow in headless Chromium
```
