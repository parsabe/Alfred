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
