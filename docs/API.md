# Alfred API

The contract for any interface (the ribbon UI, a CLI, a phone). Generated OpenAPI is always
available at `GET /openapi.json` and interactive docs at `/docs` while the server runs.

- Base URL (dev): `http://127.0.0.1:8000`
- Auth: every `/v1` route needs `Authorization: Bearer $ALFRED_API_TOKEN`. Missing or wrong → `401`.
  A client outside `IP_ALLOWLIST` (when set) → `403`.
- Errors outside a stream use FastAPI's shape: `{"detail": "..."}`.

```bash
export ALFRED=http://127.0.0.1:8000
export TOKEN=...   # the value of ALFRED_API_TOKEN
```

Status of this document: covers **M0, M1, M1.5 (voice) and hands-free voice**. Endpoints still to
come are listed at the end. How each kind of device uses them: `docs/DEVICES.md`.

The ribbon interface itself is served at `GET /` (static, no token needed to load it; it asks for
the token and sends it on every API call).

---

## Events (shared by `/v1/chat`, and later `/v1/ws` and `/v1/events`)

Every event is one JSON object:

```json
{
  "type": "token",
  "conversation_id": "1cac14dd8625475f8088c6e1e163fb2c",
  "timestamp": "2026-11-02T22:23:19.256990Z",
  "seq": 2,
  "data": {"text": "Good evening"}
}
```

`seq` increases by 1 per event within one response, so a client can detect gaps.
Over SSE, each event is sent as `event: <type>` plus `data: <the JSON above>`.

| `type` | `data` | Notes |
|---|---|---|
| `state` | `{"value": "thinking" \| "idle" \| "listening" \| "speaking" \| "awaiting_confirmation"}` | Drives the ribbon animation. First and last event of every turn. |
| `token` | `{"text": "..."}` | Text delta of Alfred's reply. Concatenate in order. |
| `error` | `{"code": "...", "message": "..."}` | `message` is safe to show the owner. A `done` follows. |
| `done` | see below | End of the turn. |
| `tool_call`, `tool_result`, `confirm_request`, `action_resolved` | — | From M2. |
| `audio` | `{"index": 1, "format": "mp3", "media_type": "audio/mpeg", "text": "Good evening, sir.", "data_b64": "…"}` | Voice mode only: one per spoken sentence, in order. Play them back to back. All audio arrives before `done`. |
| `proactive` | — | From M5. |

`done.data`:

```json
{
  "status": "complete",
  "message_id": "9f0c…",
  "provider": "anthropic",
  "model": "claude-opus-5-5",
  "used_fallback": false,
  "usage": {"input_tokens": 1830, "output_tokens": 42, "cache_read_tokens": 1500, "cache_write_tokens": 0},
  "cost_usd": 0.001572,
  "conversation_totals": {"input_tokens": 5210, "output_tokens": 160, "cost_usd": 0.0049}
}
```

`status` is `complete` or `error` (later also `awaiting_confirmation`). `cost_usd` is an estimate
from the price table in `app/core/tokens.py` (local models count as 0).

Error codes so far: `llm_unavailable` (no model configured), `llm_unreachable`,
`llm_rate_limited`, `llm_auth`, `llm_bad_request`, `llm_upstream`, `llm_refusal`,
`llm_incomplete`, `turn_timeout`, and for voice `voice_unavailable` (voice is off or its model isn't
loaded yet; the reply continues as text), `voice_auth`, `voice_quota`, `voice_rate_limited`, `voice_timeout`,
`voice_unreachable`, `voice_upstream` (synthesis stopped for this turn; text continues).

---

## `GET /health`

Liveness. Without a token: `{"status": "ok"}`. With a valid token it also reports the LLM in use
and which integrations are configured (names and booleans only, never values).

```bash
curl -s $ALFRED/health
curl -s -H "Authorization: Bearer $TOKEN" $ALFRED/health
```

```json
{
  "status": "ok",
  "version": "0.1.0",
  "llm": {"configured": true, "provider": "local", "model": "gemma3:12b", "fallback": null},
  "voice": {"tts": "piper", "stt": "whisper", "tts_status": "ready", "stt_status": "ready", "wake_word": "Alfred"},
  "integrations": {"llm.local": true, "voice.stt": true, "voice.tts": true, "weather": true, "...": false}
}
```

---

## `POST /v1/chat`

Send one message; the response is a `text/event-stream`. A keep-alive comment (`: ping`) is sent
every 15 s on long turns. Closing the connection cancels the turn.

Body:

| field | type | |
|---|---|---|
| `message` | string, 1–20000 chars | required |
| `conversation_id` | string | optional. Omit to start a new conversation; its id is on every event. |
| `output_mode` | `"text"` (default) \| `"voice"` | `voice`: Alfred answers in plain speakable sentences, and the stream also carries `audio` events (one per sentence) in his voice. |
| `attachments` | array | reserved; must be empty for now (`422` otherwise) |

```bash
curl -sN $ALFRED/v1/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "Good evening, Alfred.", "output_mode": "text"}'
```

```
event: state
data: {"type":"state","conversation_id":"1cac…","seq":1,"data":{"value":"thinking"}, ...}

event: token
data: {"type":"token","conversation_id":"1cac…","seq":2,"data":{"text":"Good evening, sir."}, ...}

event: done
data: {"type":"done","conversation_id":"1cac…","seq":3,"data":{"status":"complete", ...}, ...}

event: state
data: {"type":"state","conversation_id":"1cac…","seq":4,"data":{"value":"idle"}, ...}
```

Continue the conversation by passing the id back:

```bash
curl -sN $ALFRED/v1/chat -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "And tomorrow?", "conversation_id": "1cac14dd8625475f8088c6e1e163fb2c"}'
```

Status codes before the stream starts: `401`, `403`, `404` (unknown `conversation_id`), `422`
(validation). Problems *during* the turn arrive as an `error` event, then `done` with
`status: "error"`.

JavaScript (browser `EventSource` cannot POST, so read the stream with `fetch`):

```js
const res = await fetch(`${ALFRED}/v1/chat`, {
  method: "POST",
  headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
  body: JSON.stringify({ message, conversation_id, output_mode: "voice" }),
});
const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
let buf = "";
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += value;
  let i;
  while ((i = buf.indexOf("\n\n")) >= 0) {
    const block = buf.slice(0, i); buf = buf.slice(i + 2);
    const data = block.split("\n").filter(l => l.startsWith("data:")).map(l => l.slice(5)).join("\n");
    if (data) handle(JSON.parse(data));   // {type, conversation_id, seq, data}
  }
}
```

Voice mode, as the ribbon interface uses it:

```bash
curl -sN $ALFRED/v1/chat -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "Is the car ready?", "output_mode": "voice"}'
```

```
event: state   … {"value":"thinking"}
event: token   … {"text":"Good evening, sir. "}
event: audio   … {"index":1,"format":"mp3","media_type":"audio/mpeg","text":"Good evening, sir.","data_b64":"SUQz…"}
event: token   … {"text":"The car is ready."}
event: audio   … {"index":2, … "text":"The car is ready.", …}
event: done    … {"status":"complete", …}
event: state   … {"value":"idle"}
```

---

## `POST /v1/tts`

Speak any text in Alfred's voice. Body `{"text": "..."}` (1–2000 chars). Returns the audio bytes
(`audio/wav` from Piper). `503` if voice is off (`TTS_PROVIDER=none`), `502` if synthesis fails.

```bash
curl -s $ALFRED/v1/tts -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "Good evening, sir."}' --output alfred.wav
```

## `POST /v1/stt`

Transcribe recorded speech (Whisper, on the server). Multipart form: `audio` (file, max 10 MB; webm/ogg/mp4/
wav/mp3) and optional `language` (e.g. `en`, `de`). Returns `{"text": "..."}`. `503` if
hearing is off, `413` too large, `422` empty, `502` provider error.

```bash
curl -s $ALFRED/v1/stt -H "Authorization: Bearer $TOKEN" -F audio=@question.webm -F language=en
```

```json
{"text": "Is the car ready?"}
```

---

## `POST /v1/ask`

One question in, one JSON answer out. No streaming, so it suits **Apple Shortcuts / Siri on
iPhone and Apple Watch, Android HTTP-shortcut apps, Wear OS, home automation**.

| field | | |
|---|---|---|
| `message` | string, 1–4000 | required |
| `conversation_id` | id \| `"latest"` (default) \| `null` | `latest` continues the conversation used in the last 30 minutes, or starts a new one |
| `voice` | bool, default `false` | answer in speakable sentences and include Alfred's voice as `audio_b64` |

```bash
curl -s $ALFRED/v1/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "What should I wear today?"}'
```

```json
{"reply": "A light coat, sir; it's 12 degrees with rain after four.",
 "conversation_id": "1cac…", "error": null, "audio_b64": null, "audio_media_type": null}
```

If the model fails, `reply` holds a safe explanation and `error` the same message.

---

## `WS /v1/voice`: hands-free conversation

A continuous, two-way voice session. The client streams its microphone; the server listens for
the wake word, transcribes locally (Whisper), answers with Gemma, speaks (Piper), and handles
interruptions. The web app and `clients/speaker.py` both use it; any device that can open a
WebSocket and play WAV audio can.

**Auth**: header `Authorization: Bearer <token>`, or (browsers, which can't set headers) the
subprotocols `["bearer", "<token>"]`. Bad token → closed with code `4401`; blocked IP → `4403`;
bad hello → `4400`.

**Client → server**

| message | meaning |
|---|---|
| `{"type":"hello","mode":"wake","conversation_id":null,"sample_rate":16000,"barge_in":true}` | first message. `mode`: `wake` (answer “Alfred, …”), `ptt` (only after a `wake` message; nothing is heard before that), `always` (every sentence is for Alfred). `barge_in` optional per-device override. |
| binary frames | microphone audio: **16 kHz, mono, 16-bit little-endian PCM**, any chunk size (20 ms is typical) |
| `{"type":"wake"}` | tap-to-talk: listen now without the wake word (interrupts him if he's speaking) |
| `{"type":"text","message":"…"}` | a typed message |
| `{"type":"playback","state":"ended"}` | the client finished playing this turn's audio (send after `done` once the audio queue drains) |
| `{"type":"interrupt"}` / `{"type":"stop"}` / `{"type":"sleep"}` | stop him and listen / stop him and sleep / sleep |
| `{"type":"mode","mode":"ptt"}` | switch mode |

**Server → client**: JSON events with the usual envelope (`type`, `conversation_id`, `timestamp`,
`seq`, `data`):

| `type` | `data` | |
|---|---|---|
| `ready` | `{"mode","wake_word","follow_up_window_s","barge_in","stt","tts","sample_rate"}` | after hello |
| `state` | `{"value": "idle"\|"listening"\|"thinking"\|"speaking"}` | `idle` = asleep, waiting for his name; `listening` = in conversation, no name needed |
| `user_speech` | `{"value": "start"\|"end"}` | someone is talking |
| `transcript` | `{"text": "Alfred, is the car ready?", "command": "Is the car ready?"}` | a sentence he is acting on (sentences not addressed to him are dropped silently) |
| `token`, `audio`, `done`, `error` | as in `/v1/chat` | `done.status` is `ack` for his short “Yes, sir?” |
| `duck` / `unduck` | `{}` | lower / restore his voice: you started talking over him / it was only his echo |
| `interrupt` | `{"reason": "speech"\|"tap"\|"text"\|"client"}` | stop playing his audio now |

A typical exchange:

```
→ hello {mode: wake}                      ← ready, state idle
→ (audio) "The weather is nice."          (ignored: not addressed to him)
→ (audio) "Alfred, is the car ready?"     ← user_speech start/end, state thinking,
                                            transcript, token…, audio…, state speaking, done
→ playback ended                          ← state listening   (follow-up, no name needed)
→ (audio) "And the umbrella?"             ← transcript, state thinking, … done
→ playback ended                          ← state listening … 8 s later: state idle
```

Try it from a terminal: `python clients/speaker.py --server $ALFRED --token $TOKEN`.

---

## `GET /v1/conversations`

Newest first, cursor-paginated by `updated_at`.

| query | default | |
|---|---|---|
| `limit` | 20 | 1–100 |
| `before` | — | pass the previous page's `next_before` |

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$ALFRED/v1/conversations?limit=2"
```

```json
{
  "items": [
    {"id": "1cac…", "title": "Good evening, Alfred.", "created_at": "…", "updated_at": "…",
     "input_tokens": 1830, "output_tokens": 42, "cost_usd": 0.0016}
  ],
  "total": 7,
  "next_before": "2026-11-02T22:23:20.625537Z"
}
```

`next_before` is `null` on the last page.

## `GET /v1/conversations/{id}`

Full history. `include_context=true` also returns the per-turn context notes (time, output mode)
Alfred was given.

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$ALFRED/v1/conversations/1cac14dd8625475f8088c6e1e163fb2c"
```

```json
{
  "id": "1cac…", "title": "Good evening, Alfred.", "created_at": "…", "updated_at": "…",
  "input_tokens": 1830, "output_tokens": 42, "cost_usd": 0.0016,
  "messages": [
    {"id": "…", "seq": 1, "role": "user", "text": "Good evening, Alfred.",
     "content": [{"type": "text", "text": "Good evening, Alfred."}], "usage": null, "created_at": "…"},
    {"id": "…", "seq": 3, "role": "assistant", "text": "Good evening, sir.",
     "content": [{"type": "text", "text": "Good evening, sir."}],
     "usage": {"input_tokens": 1830, "output_tokens": 42, "cost_usd": 0.0016, "model": "claude-opus-5-5"},
     "created_at": "…"}
  ]
}
```

`404` if not found.

## `DELETE /v1/conversations/{id}`

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "$ALFRED/v1/conversations/1cac14dd8625475f8088c6e1e163fb2c" -w "%{http_code}\n"
```

`204` on success, `404` if not found.

---

## Coming next

| Milestone | Endpoints |
|---|---|
| M2 Tools | `GET /v1/tools`, `GET /v1/actions/pending`, `POST /v1/actions/{id}/approve`, `POST /v1/actions/{id}/reject` |
| M3 Memory | `GET/POST/DELETE /v1/memory` |
| M5 Proactive | `GET /v1/events` |
| M6 Hardening | `WS /v1/ws`, `GET /v1/export`, `DELETE /v1/data`, rate limits |
