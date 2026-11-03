# Milestone reports

## M0 + M1 — Scaffold and conversation core (2026-11-02)

**Built**
- Config from env with derived feature flags; missing optional keys never crash startup. Startup
  refuses an `ALFRED_API_TOKEN` shorter than 32 chars.
- Bearer auth (constant-time), optional IP allowlist, CORS restricted to `CORS_ORIGINS`.
- Log redaction: exact values of every configured secret + generic credential patterns, applied to
  messages, args and tracebacks on every handler (uvicorn's included).
- SQLite via async SQLAlchemy with Alembic migrations (run automatically at startup).
- LLM layer: provider-neutral types; Anthropic (adaptive thinking, effort, prompt caching,
  verbatim replay of thinking blocks, mid-conversation system messages for per-turn context,
  server-side refusal fallback), OpenAI, local OpenAI-compatible (Ollama, default `gemma3:12b`),
  scripted fake. Failover to a fallback provider only before any text has been streamed. If the
  primary isn't configured, the fallback is promoted (so: Anthropic once a key exists, Gemma until then).
- Agent turn: append-only history, per-turn context note (time, output mode), budgeted context
  window that never splits a turn, step cap and turn timeout, safe error events, usage + estimated
  cost per message and per conversation.
- API: `GET /health`, `POST /v1/chat` (SSE), `GET/GET/DELETE /v1/conversations`. Documented in `docs/API.md`.
- Tooling: `make dev|test|check|fmt|token|migrate`, ruff, mypy (strict on `app/core`),
  pre-commit (incl. a hook that refuses to commit `.env`), GitHub Actions CI, Dockerfile + compose
  (with an optional Ollama service).

**Tests** (43): config/redaction, persona rendering and hot reload, context windowing, cost,
Anthropic and Ollama adapters replayed against wire-format streams (no network), failover rules,
and API integration (auth, CORS, IP allowlist, SSE event order/seq, persistence, history,
voice-mode hint, timeout, safe errors, pagination, delete, startup without any LLM).

**Verified by hand:** `uvicorn` boots with only the token set; `/health` and `/v1/chat` respond; the
token never appears in logs; with no Ollama reachable, chat returns `llm_unreachable` cleanly.

**Not verified here:** live Anthropic/Gemma/Fish Audio calls (no keys; the sandbox blocks
`api.fish.audio`, `ollama.com` and `huggingface.co`), and `docker build` (no Docker daemon).

**Known limitations:** no tools yet (M2); no summarization of very long conversations yet (M3;
old turns simply fall out of the window); attachments rejected; single-process only.

## M1.5 + interface — Live voice conversation (2026-11-03)

Pulled forward at the owner's request ("where is the live chat?").

**Built**
- Fish Audio adapters (`app/voice/fish.py`): TTS (`/v1/tts`, voice `FISH_AUDIO_VOICE_ID`,
  model `FISH_AUDIO_MODEL`) and STT (`/v1/asr`), with timeouts and retry on 429/5xx.
- Voice pipeline (`app/core/voice_turn.py`): in `output_mode=voice`, the reply is split into
  sentences as it streams (`app/voice/chunker.py`, abbreviation/decimal aware, early first clause)
  and each sentence is synthesized in order and sent as an `audio` event; `done` follows the last
  audio. If synthesis fails, one `error` event is sent and the reply continues as text.
- Endpoints: `POST /v1/tts`, `POST /v1/stt`; `/health` reports voice providers.
- The ribbon interface (`web/`, served at `/`, no build step): canvas renderer reproducing
  `docs/interface-reference.png` (additive layered lobes, crossing teal strand, bloom), animated
  per state: idle hairline, listening follows the mic, thinking undulates, speaking follows
  Alfred's actual audio. Tap/Space to talk, conversation mode, interruption, captions, typing
  (`/`), settings (token stays in the browser), browser or server speech recognition,
  browser-voice fallback when the server has no voice.
- `make demo`: the whole interface with no keys (stand-in brain, temporary DB).

**Tests**: 60 Python tests (17 new: chunker, Fish request shapes/retries/auth, voice streaming
order, failure fallback, endpoints, static UI) + `make e2e`, which drives headless Chromium with a
fake microphone through a typed turn, a spoken turn in conversation mode, and an interruption.

**Not verified here:** real Fish Audio calls (host blocked in this sandbox): the request format
follows Fish's public API and is unit-tested, but the first real call happens on your machine.
Browser speech recognition can't run headless, so the e2e test uses the server path.

**Known limitations:** Chrome's built-in recognition sends audio to Google; choose "Alfred's
server" in settings to keep audio with Fish Audio instead. No wake word yet (tap to talk).

## Hands-free, fully local, every device (2026-11-03)

Owner's requests: answer to "Alfred, …" without tapping; interrupt him by talking; no API keys,
Gemma 3 12B only; one Ubuntu server (12 GB RAM) that phone, watch and other devices connect to.

**Built**
- `WS /v1/voice` + `app/core/voice_session.py`: continuous microphone stream → server-side
  speech detection (`app/voice/vad.py`: WebRTC VAD + adaptive noise floor, pre-roll, end of speech)
  → local transcription → wake word (`app/voice/wake.py`: start or end of a sentence, fuzzy:
  "Alfred's", "Elfred", "Al Fred") → Gemma → Piper → audio back. States: asleep → thinking →
  speaking → follow-up window (8 s, no name needed) → asleep. "Alfred?" alone → "Yes, sir?".
  Sentences not addressed to him are dropped immediately (never stored or forwarded).
- Barge-in: talking over him ducks his voice at 350 ms and interrupts at 1.2 s or once the
  sentence is recognised; his own voice echoing back is detected and ignored. Interrupted
  replies are recorded as such in the history so he knows he was cut off. Per-device switch.
- Local providers (`app/voice/local.py`): faster-whisper (`base` by default for 12 GB) and Piper
  (`en_GB-alan-medium`), downloaded once and loaded in the background; `/health` shows status.
- Gemma by default; agent keeps context within Ollama's window (`LOCAL_LLM_CONTEXT_TOKENS`).
  `.env.example` contains no API keys; the Fish Audio key was removed from the sandbox `.env`.
- Devices: installable web app (manifest, service worker, icons), hands-free with screen wake
  lock; `POST /v1/ask` (one-shot JSON, `conversation_id: "latest"`) for Apple Watch Shortcuts,
  Wear OS, automations; `clients/speaker.py` (Pi/desktop speaker, reconnects, mutes mic while
  speaking unless `--barge-in`); `clients/alfred_cli.py`; `docs/DEVICES.md`.
- `deploy/install.sh` (Ubuntu): Ollama + model + context/keep-alive config, Alfred in
  `/opt/alfred` as a hardened systemd service, models pre-downloaded, optional Tailscale HTTPS.

**Tests**: 92 Python tests (wake-word cases, segmentation, the full hands-free state machine over
a real WebSocket: ignore/wake/ack/follow-up/barge-in/echo/push-to-talk/auth, `/v1/ask`), the
headless-Chromium e2e (fake microphone through the real page, worklet, server VAD and playback:
ignored remark → wake → interruption → follow-up → sleep → typing), and the speaker client
against the demo server.

**Not verified here** (this sandbox can't download models or reach Ollama): real Whisper
recognition of "Alfred", Piper's voice, and Gemma's answers. The adapters follow the installed
libraries' APIs; the first real run is on the server. `deploy/install.sh` was syntax-checked
but not run (no Ubuntu VM with systemd here).

## Branch layout and native clients (2026-11-03)

At the owner's request the project now lives on separate, never-merged branches:

| Branch | Contents | Verified |
|---|---|---|
| `kernel` | server: agent, voice session, local Gemma/Whisper/Piper, API, installer, tests; fetches the interface from `web` | 93 tests + headless-browser e2e against the interface fetched from GitHub; CI green |
| `web` | ribbon interface (PWA) | served and driven by the kernel e2e |
| `android` | Kotlin app: Alfred's interface from your server, microphone bridged to Android permissions (server origin only), keep-awake, change-server shortcut | CI on GitHub: unit tests + debug APK build green |
| `linux` | desktop client `alfred` (hands-free, app window, chat, status, devices), PyInstaller build, per-user installer, app-menu entries, optional user service | 19 tests; compiled binary tested here against the demo server (status, chat, hands-free over a fake microphone, rejected token) |
| `windows` | same client as `Alfred.exe`, `build.ps1`, Inno Setup installer (per user, Start menu, autostart, PATH) | 19 tests here; compile and installer on GitHub's Windows CI |
| `docs` | BRIEF, PLAN, API, DEVICES, MILESTONES | — |
| `master` | README linking every branch | — |

The Linux and Windows branches carry the same client code; a change to one should be copied to
the other.
