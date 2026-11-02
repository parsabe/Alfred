# Alfred — Implementation Plan

Status: **approved 2026-11-02.** Owner decisions are recorded in §12. Progress: `docs/BRIEF.md` → Status.

Source of truth for requirements: `docs/BRIEF.md`. This plan says *how*; where it deviates from or
adds to the brief, it says so and marks it **[Deviation]** or **[Addition]**. Every assumption is
marked **[A#]** and collected in §11.

---

## 1. What gets built

A single-owner, async Python service exposing a versioned HTTP/SSE/WebSocket API. It runs an agent
loop over a pluggable LLM, calls tools behind a risk-gated confirmation flow, remembers facts about
the owner, acts proactively on a schedule, and speaks in the owner's Fish Audio voice.

```
 Interface (orb UI / CLI / phone)
        │  Bearer token, JSON + SSE / WS
        ▼
 ┌──────────── api/ (routing, validation, auth only) ─────────────┐
 │ chat  conversations  actions  memory  tools  events  voice  health │
 └─────────────────────────────┬────────────────────────────────────┘
                               ▼
 core/agent ── context (budgeted) ── persona ── events (typed stream)
     │              │                                   │
     ▼              ▼                                   ▼
  llm/ (provider protocol)     memory/ (facts, vectors, summaries)
  anthropic · openai · local · fake
     │
     ▼
  tools/ registry ──► security/guard (risk policy, untrusted wrap) ──► actions (pending/approve)
     │                                                       │
     ▼                                                       ▼
  builtin tools · integration adapters               security/audit (append-only)
                               │
 scheduler/ (APScheduler, persistent) ──► event bus ──► /v1/events + push webhook
 voice/ (TTS/STT adapters: Fish Audio) ──► audio events / endpoints
 db/ (SQLAlchemy async, Alembic)
```

Layering rules (enforced by review and an import-linter check in CI):
`api → core → {llm, tools, memory, voice} → db`. Nothing below `api` imports FastAPI.
Tools receive a `ToolContext` (owner settings, db session factory, http client), never a request.

---

## 2. Directory layout

As in the brief, plus the items marked **[Addition]**.

```
alfred/
├── app/
│   ├── main.py, config.py
│   ├── api/        chat.py conversations.py memory.py tools.py actions.py health.py
│   │               events.py  ws.py  voice.py [Addition]  data.py (export/wipe)
│   ├── core/       agent.py context.py persona.py events.py
│   │               bus.py        [Addition] in-process pub/sub feeding /v1/events + WS
│   │               tokens.py     [Addition] token estimation + cost table
│   ├── llm/        base.py anthropic.py openai.py local.py fake.py  router.py (failover)
│   ├── tools/      base.py registry.py builtin/*.py  adapters/ (calendar, email, ... backends)
│   ├── memory/     short_term.py long_term.py vector.py embeddings.py
│   ├── voice/      [Addition] base.py fish.py sentence_chunker.py
│   ├── scheduler/  jobs.py quiet_hours.py
│   ├── security/   auth.py guard.py audit.py redaction.py ratelimit.py
│   └── db/         models.py session.py migrations/ (alembic)
├── prompts/alfred_system.md
├── web/            [Addition, pending decision D1] the orb interface (§9)
├── tests/          unit/ integration/ injection/ fixtures/
├── docs/           BRIEF.md PLAN.md API.md TOOLS.md interface-reference.png
├── .env.example  Dockerfile  docker-compose.yml  Makefile  pyproject.toml  .pre-commit-config.yaml
└── CLAUDE.md       pointer for future sessions → docs/BRIEF.md Status
```

---

## 3. Dependencies (and why)

Managed with **uv**; Python **3.12+** (container has 3.13; Docker image pins 3.12-slim).

| Package | Why |
|---|---|
| `fastapi`, `uvicorn[standard]` | Brief default; async, OpenAPI for free. |
| `sse-starlette` | Correct SSE framing, keep-alive pings, client-disconnect detection. |
| `pydantic` v2, `pydantic-settings` | Config + API schemas; feature flags derived from which keys are set. |
| `sqlalchemy[asyncio]` 2.x, `aiosqlite`, `alembic` | Brief default. Postgres = swap `DATABASE_URL` to `postgresql+asyncpg://` (add `asyncpg` extra). No SQLite-only SQL outside `vector.py`. |
| `anthropic` | Official SDK for the primary provider. |
| `openai` | OpenAI provider **and** the `local` provider (Ollama/vLLM/LM Studio speak the OpenAI wire format). |
| `httpx` | All other outbound HTTP (Fish Audio, weather, Home Assistant, webhooks). One shared client with timeouts. |
| `tenacity` | Uniform retry-with-backoff + jitter on every external call. |
| `apscheduler` 3.10.x | Brief default. 3.x, not 4.x (4.x is still pre-release). Uses a SQLAlchemy job store on the same DB file. |
| `sqlite-vec` | Local vector index inside the same SQLite file — no extra service. |
| `fastembed` (optional extra `[local-embeddings]`) | Local CPU embeddings (ONNX, ~130 MB model) when no embeddings API key is configured. |
| `python-dateutil` | Natural date arithmetic for `get_datetime`, `tasks`, `reminders` (stdlib `zoneinfo` for tz). |
| dev: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`, `pre-commit`, `import-linter`, `locust` (M6) | Testing, mocking outbound HTTP, quality gates, load test. |

Deliberately **not** used: LangChain/LlamaIndex (hide the loop we must control for confirmation and
injection defence), `slowapi` (a 40-line token bucket suffices for one owner), `simpleeval` (the
calculator is a small whitelisted AST walker we can test exhaustively), Chroma (a second datastore
for no gain at personal scale).

---

## 4. LLM layer

`LLMProvider` protocol: `stream(request) -> AsyncIterator[LLMEvent]`, `complete(request)`,
`supports_tools`, `name`. Requests and events are provider-neutral (our own `Message`,
`ToolSpec`, `TextDelta`, `ToolCall`, `Usage`), so the agent never sees provider types.

- **Anthropic (default).** `LLM_MODEL` defaults to `claude-opus-5-5` **[A1]**. Streaming always,
  adaptive thinking, `output_config.effort` configurable (`LLM_EFFORT`, default `medium`, which is this
  model's own default; `low` is a sensible voice-mode setting). Opus 5.5 specifics the adapter must
  respect:
  - forced `tool_choice` (`any`/`tool`) is rejected → always `auto`, tools marked `strict: true`;
  - thinking cannot be disabled; no assistant prefill;
  - **preserved thinking**: thinking blocks must be replayed unchanged and history must be
    append-only within a conversation. This shapes §6 (we never rewrite earlier turns in place);
  - server-side refusal fallback (`fallbacks: "default"`) enabled; this is separate from our own
    provider failover.
- **OpenAI** and **local**: same adapter class, different base URL/key. `local` assumes the server
  supports OpenAI-style tool calling; if it does not, tools are disabled for that provider and
  Alfred says so.
- **Fake**: scripted event sequences (text, tool calls, errors, delays) for deterministic tests.
- **router.py**: primary → fallback on timeout/5xx/connection error/429-after-retries, *only if no
  tokens have been emitted to the client yet* for that step (otherwise we error rather than produce a
  stitched answer). Fallback use is reported in the `done` event.
- **Cost**: per-model price table in `core/tokens.py` (USD per MTok in/out/cache-read), overridable
  via `LLM_PRICE_OVERRIDES`. Usage comes from provider responses, not estimates.

---

## 5. Agent loop (`core/agent.py`)

One *turn* = one owner message until Alfred yields a final answer or blocks on a confirmation.

1. Persist owner message → build context (§6) → call provider (streaming).
2. Stream `token` events. On tool calls: emit `tool_call`, then route through `guard.policy`:
   - `read` → execute. Independent `read` calls in one step run concurrently (`asyncio.gather`,
     per-tool timeout).
   - `write` (not auto-approved) / `critical` → create `PendingAction` (10 min TTL), emit
     `confirm_request`, **end the turn** with `done{status:"awaiting_confirmation"}`. The model gets
     no result yet.
3. Tool results are truncated (`TOOL_RESULT_MAX_CHARS`, default 8 000), wrapped as untrusted data
   (§10), fed back; loop until no tool calls, `MAX_AGENT_STEPS` (8) reached, or `TURN_TIMEOUT_S`
   (120) elapsed. The step cap produces a graceful "I've stopped here; this is where things stand" answer.
4. **Resumption**: `POST /v1/actions/{id}/approve` executes the tool (with edited args if supplied,
   re-validated against the schema, and re-checked for risk), stores the tool result, and resumes the
   agent for that conversation. The continuation streams on the approve response (SSE) **and** on the
   conversation's WS/`/v1/events` subscribers. Reject/expiry stores a tool result saying "declined by
   owner" / "expired" and resumes so Alfred can acknowledge.
5. Every tool call, decision, and result status → audit log.

Mixed steps (some `read`, some `write` calls in one model response): reads execute; writes go
pending; the turn pauses until all pending ones in that step are decided. **[A2]**

---

## 6. Context, memory and summarization

Context is assembled for every LLM call in this order (brief §8), with prompt-caching in mind:

| Slot | Content | Placement |
|---|---|---|
| 1 | Persona (`prompts/alfred_system.md`, rendered) **minus volatile vars** | top-level `system`, cached |
| 2 | Owner profile (pinned facts) | top-level `system`, cached (changes rarely) |
| 3 | Conversation summary (if any) | first user message of the window |
| 4 | Recent turns, verbatim (including thinking/tool blocks) | messages |
| 5 | Per-turn context: current time, `output_mode`, top-k relevant memories | system message appended after the new user message |

**[Deviation]** The brief puts `{current_datetime}` in the system prompt. A timestamp in the system
prompt changes on every call, which defeats prompt caching and edits the history prefix. We still support the
`{current_datetime}` variable, but the renderer moves volatile values into slot 5. The persona file
is unchanged and you can still edit it freely.

- **Long-term facts** (`memory_facts` table): text, category, source (`explicit`|`extracted`),
  confidence, created/updated, embedding. Explicit = `memory_save` tool or `POST /v1/memory`.
  Extracted = an end-of-turn background pass (small model, `MEMORY_EXTRACTION=on|off`, default **off**
  until you decide, **D4**) proposing durable facts, deduped by similarity.
  Sensitive categories (passwords, account numbers, health) are refused unless explicitly requested.
- **Vector recall** (`vector.py`): `VectorStore` protocol; default `sqlite-vec`. Indexes facts and
  notes. **Embeddings** (`EMBEDDINGS_PROVIDER`): `openai` | `local` (fastembed) | `none`.
  With `none`, recall falls back to SQLite FTS5 keyword search, so memory works with zero extra keys. **[A3]**
- **Budget**: `CONTEXT_BUDGET_TOKENS` (default 60 000, well below the window, for cost and latency).
  Estimation is heuristic (chars/3.5, +15 % margin). Exact usage is reconciled from responses.
- **Summarization** (append-only, preserved-thinking safe): when the window exceeds budget, older
  turns are summarized **at a turn boundary** into a new summary *segment*; the next request starts
  a fresh window `[summary, last N turns]`. We never mutate a message in an existing window.
  Full history stays in the DB and in `GET /v1/conversations/{id}`.

---

## 7. Tools

`Tool` base: `name`, `description`, `input_schema` (generated from a Pydantic model), `risk`
(`read`|`write`|`critical`, may be **per-operation**, e.g. `smart_home.get_state`=read,
`smart_home.call_service`=write), `requires` (settings that must be present), `async run(args, ctx)
-> ToolResult(content, summary, data)`. Registry auto-discovers `tools/builtin/*.py`, drops tools
whose `requires` aren't met, and exports schemas for `/v1/tools` and the persona's `{enabled_tools}`.
Adding a tool = one file (documented in `docs/TOOLS.md`).

Multi-operation tools (`notes`, `tasks`, `calendar`, `email`) are split into one model-facing tool
per operation (`notes_create`, `notes_search`, …) so each has an exact schema and its own risk level. **[A4]**

Core (M2): `get_datetime`, `calculator`, `memory_save/search/forget`, `notes_*`, `tasks_*`,
`reminders_*` (scheduler wiring lands in M5; M2 stores them). Risk defaults: create/update = `write`,
delete = `write`, `memory_forget` = `write`.

Integrations (M4), each behind an adapter interface so the backend can be swapped:
web search (Tavily/Brave/SerpAPI) + fetch (httpx + readability-style extraction, size-capped),
weather (Open-Meteo needs no key, so it could be on by default; OpenWeather if a key is given **[A5]**),
calendar (Google / CalDAV), email (Gmail / IMAP+SMTP; `email_send` = critical), messaging (Telegram;
critical), Home Assistant, files (sandbox: resolve + `is_relative_to`, reject symlinks escaping the
root, size limits), code_exec (off; Docker `--network none --memory 256m --cpus 0.5 --read-only`,
timeout; requires Docker socket, so it is documented as a security trade-off).

Google OAuth needs a one-time consent flow → a small `GET /v1/integrations/google/connect`
+ callback, refresh token stored encrypted at rest (`ALFRED_SECRET_KEY`, Fernet). **[Addition]**

---

## 8. Voice (Fish Audio)

**[Deviation]** The brief lists voice as optional M7. Your interface is voice-first and you've
already supplied a voice, so I propose doing it straight after M1 (as **M1.5**). Decision **D2**.

- `voice/base.py`: `TTSProvider.synthesize(text) -> bytes`, `stream(text_chunks) ->
  AsyncIterator[bytes]`; `STTProvider.transcribe(audio) -> str`.
- `voice/fish.py`: `POST https://api.fish.audio/v1/tts`, headers `Authorization: Bearer
  $FISH_AUDIO_API_KEY`, `model: $FISH_AUDIO_MODEL` (default `s2.1-pro-free`), body `{text,
  reference_id: $FISH_AUDIO_VOICE_ID, format: "mp3"}`. Response = audio bytes.
  STT via Fish Audio's ASR endpoint (`POST /v1/asr`) **[A6]**, or the browser's own speech
  recognition (the interface can send text instead of audio).
- **Low-latency pipeline** for `output_mode=voice`: LLM tokens → `sentence_chunker` (splits on
  sentence ends; never splits numbers, abbreviations, or URLs) → TTS per sentence, pipelined (sentence
  *n+1* synthesizes while *n* plays) → `audio` events `{seq, format:"mp3", data_b64}` on the
  same stream as `token`. The first audio typically arrives about one sentence after the first token.
  If Fish Audio's streaming WebSocket proves stable we switch the adapter to it later; no API change. **[A6]**
- Voice mode also adds a persona hint (spoken style: no Markdown, numbers and times written to be said aloud,
  short sentences).
- Endpoints: `POST /v1/tts` (text → audio/mpeg), `POST /v1/stt` (audio → text).
- If `FISH_AUDIO_API_KEY` is missing, `output_mode=voice` returns plain-text responses + a single
  `error{code:"voice_unavailable"}` event. The service never crashes.

---

## 9. Your interface (the ribbon in `docs/interface-reference.png`)

I read the image as a **voice-orb UI**: a thin luminous ribbon on pure black; warm band
(white → amber → red) arching above, cool band (green → cyan → blue) dipping below, pinched to a
point at both ends, with soft bloom. The backend supports it as follows:

- **State events** **[Addition]**: `state{value: idle|listening|thinking|speaking|awaiting_confirmation}`
  so the ribbon can change its motion without guessing.
- **Amplitude**: computed client-side from the audio being played (Web Audio `AnalyserNode`) and from the mic
  while listening, so no extra server traffic.
- **D1 (decision)**: the brief says you build the UI yourself. If you'd like, I can also build it:
  `web/`, a dependency-free page with a WebGL2 fragment shader that reproduces the
  ribbon (layered additive sine bands, bloom, pinched envelope) driven by state + amplitude, plus
  push-to-talk / wake button, confirmation cards, and a minimal transcript drawer. Served by
  FastAPI at `/` in production, Vite only for dev. About one milestone of work.

Behaviour sketch: **idle** = near-flat hairline, slow breathing; **listening** = ribbon follows mic
amplitude, cool band leads; **thinking** = slow travelling undulation, warm band brightens;
**speaking** = ribbon height follows Alfred's voice amplitude (full image look); **awaiting
confirmation** = amber pulse and a card with Approve / Reject.

---

## 10. Security

- **Auth**: `Authorization: Bearer` on every route except `/health` liveness (whose integration list
  is shown only when authenticated **[A7]**); `hmac.compare_digest`; optional `IP_ALLOWLIST`
  (CIDRs). WS authenticates via `Sec-WebSocket-Protocol` or first message, not the query string
  (query strings end up in logs). Startup refuses an empty or short (< 32 chars) `ALFRED_API_TOKEN`.
- **Injection defence (three layers)**: (1) persona rule; (2) structure: every tool result goes to the model
  as `<untrusted_tool_output tool="…" id="…">…</untrusted_tool_output>`, with any closing-tag lookalikes
  in the content neutralised, plus a standing reminder in slot 5; (3) the confirmation gate:
  side effects are impossible without the owner's approval, regardless of what the model decides.
  The M6 injection suite drives the fake provider to *obey* injected text and asserts that nothing
  executes; it then runs the same fixtures against the real model as an opt-in eval (`make eval-injection`).
- **Audit**: `audit_log` table, insert-only (no update/delete in the repository API; SQLite trigger
  blocks UPDATE/DELETE), args passed through redaction. Survives `DELETE /v1/data` only if
  `AUDIT_RETAIN_ON_WIPE=true` (**D5**).
- **Redaction**: logging filter masks values of every `*_KEY`, `*_TOKEN`, `*_SECRET` setting plus
  patterns (`sk-…`, `sk-ant-…`, `sk-fish-…`, bearer headers, JWTs). Unit-tested with the real setting
  names. Secrets are `SecretStr` throughout, so `repr` never leaks them.
- **Rate limits**: token bucket per route group (chat 30/min, actions 60/min), configurable.
- **CORS**: exact origins from `CORS_ORIGINS`; no wildcard with credentials.
- **Export/wipe**: `GET /v1/export` (JSON bundle of everything, secrets excluded), `DELETE /v1/data`
  (requires `{"confirm":"DELETE"}` body).

---

## 11. Assumptions

- **A1** Default LLM is Anthropic `claude-opus-5-5` at effort `medium` (~$4/$20 per MTok in/out). Switching
  to `claude-sonnet-5-5` (cheaper/faster, good for voice) or `claude-haiku-5-5` is a one-line `.env`
  change. Memory extraction/summarization use `LLM_UTILITY_MODEL` (default `claude-haiku-5-5`).
- **A2** Mixed read/write steps execute reads immediately and pause on writes.
- **A3** Memory works with no embeddings key (FTS5); semantic quality improves with `openai` or `local`.
- **A4** One model-facing tool per operation; `TOOLS.md` shows the pattern.
- **A5** Weather can default to Open-Meteo (no key, so enabled by default). Tell me if you'd rather it stay off.
- **A6** Fish Audio specifics beyond what you gave me (`reference_id` body field, `/v1/asr`, streaming
  WS) are from their public API and **unverified**: this sandbox's network policy blocks
  `api.fish.audio`, so I could not test your key here. Verified in M1.5 once the host is allowed.
- **A7** `/health` without a token returns only `{"status":"ok"}`; integration names need auth
  (the brief allows names publicly; I'd rather not advertise them).
- **A8** Single owner, single process. The event bus is in-process; multi-worker deploys would need
  Redis. Out of scope, but noted.
- **A9** "He/his" in the persona file stays as you wrote it. It's your text, and you can edit it.

---

## 12. Decisions

**Owner answers (2026-11-02):** plan approved as recommended: D1 yes (I build the ribbon UI),
D2 yes (voice is M1.5), D4 off, D5 keep. D3 repo: `github.com/parsabe/alfred`.
D6: everything **except Gmail and Telegram** (web search, weather, Google Calendar, IMAP/SMTP
email, Home Assistant, files). D7: Berlin / Europe/Berlin; Alfred addresses the owner as "sir",
Alfred Pennyworth style; owner's own name not yet given (`OWNER_NAME` empty → "sir").
LLM: Anthropic when the owner supplies a key; until then **Gemma 3 12B via Ollama** (`local`).
Gemma has no 13B size; 12B is the closest.

Original questions:

| # | Question | My recommendation |
|---|---|---|
| **D1** | Should I build the ribbon interface (`web/`), or do you build it against `docs/API.md`? | I build it. You gave me the design; you can still restyle it. |
| **D2** | Move voice up to M1.5 (right after the conversation core)? | Yes. You can talk to Alfred within two milestones. |
| **D3** | Where does this code live? This sandbox is temporary and isn't linked to any GitHub repo. | Create an empty GitHub repo (e.g. `alfred`) and connect it to this environment; I push after each milestone. |
| **D4** | Automatic fact extraction from conversations: on or off by default? | Off by default; explicit "remember that…" always works. |
| **D5** | Keep the audit log when you run a full data wipe? | Keep it (it holds no message content, only tool names, redacted args, and decisions). |
| **D6** | Which integrations do you have, or want, keys for (web search provider, weather, Google, IMAP, Telegram, Home Assistant)? | Tell me before M4. |
| **D7** | Your name, timezone, location, and whether Alfred should say "sir". | Needed for `.env` and the owner profile. |

---

## 13. Milestones (revised)

Each milestone ends with: `make test` green, `make dev` boots with only `ALFRED_API_TOKEN` + one LLM
key, `docs/API.md` updated with curl examples, the Status section in `docs/BRIEF.md` updated, commit + push, and a report.

| | Scope | Done when |
|---|---|---|
| **M0** | Layout, `pyproject`, config + feature flags, `/health`, auth, redaction filter, logging, Alembic baseline, Docker/compose, Makefile, pre-commit, CI-ready pytest, this plan | Boots; auth + redaction tests pass |
| **M1** | Provider protocol + Anthropic/OpenAI/local/fake, router failover, SSE `/v1/chat`, conversations CRUD, persona loading + rendering, usage/cost in `done` | Streamed chat with persistence; fake-provider integration tests |
| **M1.5** *(D2)* | Fish TTS/STT adapters, sentence chunker, `audio` + `state` events, `/v1/tts`, `/v1/stt`, voice persona hint | A voice-mode turn streams audio; adapter tests with respx |
| **M2** | Tool base/registry, core tools, agent loop (step cap, parallel reads, timeout), confirmation flow + resume, audit log, `docs/TOOLS.md` | Confirmation test: no write/critical runs unapproved |
| **M3** | Facts, vector/FTS recall, context budgeting, segment summarization, memory endpoints + tools | Budget + summarization tests; recall tests |
| **M4** | Only the integrations from D6, each behind an adapter, Google OAuth connect flow | Per-adapter tests with recorded/mocked HTTP |
| **M5** | APScheduler persistent store, reminders (survive restart), daily briefing, `/v1/events`, push webhook, quiet hours | Restart test: a reminder scheduled before restart still fires |
| **M6** | Rate limiting, injection suite, export/wipe, `/v1/ws`, locust load test, complete `API.md` | Injection suite green; WS parity tests |
| **M7** *(D1)* | Ribbon interface in `web/` | Runs against the local backend; states, voice, and confirmations work end to end |

Event schema (SSE + WS, each with `type`, `conversation_id`, `timestamp`, `seq`):
`token` · `tool_call` · `tool_result` · `confirm_request` · `proactive` · `error` · `done`
+ **[Addition]** `state`, `audio`, `action_resolved`.

---

## 14. Risks

- **Preserved thinking / append-only history** on current Claude models is the main technical
  constraint on context management. The design (§6) handles it, but M1 includes a live check against
  the API; if it behaves differently from the docs I'll report back before M3.
- **Fish Audio latency/limits on the free model** (`s2.1-pro-free`) are unknown; the sentence
  pipeline hides most latency, and the model name is configurable.
- **Keys pasted into chat.** The Fish Audio key you pasted is now in this conversation's history.
  It is stored only in the git-ignored `.env` here and will never be committed, but I'd rotate it
  in the Fish Audio dashboard once the integration works and put the new one in `.env` yourself.
