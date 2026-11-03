# Alfred

A discreet, highly capable personal assistant that lives on **your own server** and that you
simply talk to. Say **“Alfred, …”** from your phone, watch, laptop or a speaker in the room; he
answers aloud. Talk over him and he stops to listen.

Everything runs on your machine: **Gemma 3 12B** thinks (via Ollama), **Whisper** hears,
**Piper** speaks. No API keys, no cloud services, nothing you say leaves your server.

![The ribbon](docs/interface-reference.png)

## Install (Ubuntu server)

```bash
git clone https://github.com/parsabe/Alfred.git && cd Alfred
sudo ./deploy/install.sh --tailscale
```

It installs Ollama + Gemma 3 12B, Alfred as a service that starts on boot, his hearing and voice,
and Tailscale for a private `https://` address. At the end it prints **your Alfred address** and
**access token**.

Then connect your devices. Phone app, Apple Watch, laptop and Raspberry Pi speaker are covered
step by step in **[docs/DEVICES.md](docs/DEVICES.md)**.

## Talking to him

| | |
|---|---|
| “Alfred, what's on today?” | he answers |
| “Alfred?” | “Yes, sir?”, then he listens for the next sentence |
| a follow-up within 8 s | no need to say his name again |
| talk while he speaks | he lowers his voice, then stops and listens |
| tap / Space | talk without saying his name |
| `/` | type instead |
| `,` | settings: server, token, hands-free, voice, captions, keep screen on |

His personality is in `prompts/alfred_system.md`; edits apply on the next sentence.
All settings are in `.env` (see `.env.example`; on the server: `/opt/alfred/.env`).

## Documentation

- [docs/DEVICES.md](docs/DEVICES.md): using Alfred on phone, watch, laptop, speaker; remote access
- [docs/API.md](docs/API.md): the HTTP/WebSocket API with examples, for your own integrations
- [docs/PLAN.md](docs/PLAN.md): design; [docs/BRIEF.md](docs/BRIEF.md): requirements and progress

## Development

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/); for a real brain, Ollama with
`ollama pull gemma3:12b`.

```bash
make install     # dependencies + git hooks
cp .env.example .env && make token   # put the token into ALFRED_API_TOKEN
make models      # download Whisper + Piper once
make dev         # http://127.0.0.1:8000
make demo        # the interface with a stand-in brain (no Ollama needed)
make test        # unit + integration tests
make e2e         # hands-free flow in headless Chromium (Node + Playwright)
make check       # lint + types + tests (what CI runs)
```
