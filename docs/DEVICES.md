# Using Alfred on your devices

Alfred runs on **one server** (your Ubuntu machine). Every device is just a window onto it:
the same Alfred, the same memory, the same conversations, wherever you talk to him.

```
                ┌────────────── your Ubuntu server ──────────────┐
 phone app ──┐  │  Alfred service ── Gemma 3 12B (Ollama)         │
 Apple Watch ┼──┤     ├─ Whisper (hears)   ├─ Piper (speaks)      │
 laptop ─────┤  │     └─ conversations, memory (SQLite)           │
 Pi speaker ─┘  └─────────────────────────────────────────────────┘
        private HTTPS via Tailscale (or your own domain)
```

| Device | How | Hands-free “Alfred, …” |
|---|---|---|
| iPhone / Android phone or tablet | the Alfred web app, added to the home screen | yes, while the app is open |
| Apple Watch | a Shortcut (“Hey Siri, ask Alfred”) | via Siri |
| Wear OS watch | an HTTP-shortcut app or Tasker | via the shortcut/Assistant |
| Mac / Windows / Linux | the web app installed from Chrome/Edge, or the terminal client | yes, while the app is open |
| Raspberry Pi / old computer with a mic and speaker | `clients/speaker.py` | yes, always |
| Anything else (home automation, scripts) | the HTTP API (`docs/API.md`) | — |

---

## 1. Install the server

On the Ubuntu server:

```bash
git clone https://github.com/parsabe/Alfred.git && cd Alfred
sudo ./deploy/install.sh --tailscale
```

This installs Ollama and Gemma 3 12B, Alfred as a service that starts on boot, his hearing and
voice models, and Tailscale. It prints **your Alfred address** and **access token**; keep both.

**About your hardware.** With 12 GB RAM, Gemma 3 12B fits (≈ 9 GB) next to Whisper `base` and
Piper. Without a GPU it runs on the CPU: expect him to start answering after a few seconds and to
speak at roughly reading pace on a modern 8-core CPU, slower on older ones. If that's too slow:
`sudo ALFRED_MODEL=gemma3:4b ./deploy/install.sh` (about three times faster, a little less clever).
An NVIDIA GPU with ≥ 10 GB is picked up automatically and makes 12B fast.

Update later with `git pull && sudo ./deploy/install.sh --tailscale`. Your settings and
conversations are kept.

## 2. Reach it from your devices

Phones only allow the microphone on **https://** pages, so Alfred needs an HTTPS address.

### Recommended: Tailscale (private, free for personal use, nothing exposed to the internet)

1. `install.sh --tailscale` already put the server on your tailnet and printed
   `https://<server>.<tailnet>.ts.net`. If it asks, enable **HTTPS certificates** once in the
   Tailscale admin console (DNS → HTTPS Certificates).
2. Install the Tailscale app on your **phone**, **laptop** and anything else, and sign in with
   the same account. That's it: the address works on all of them, from home or anywhere.
3. Only your own devices can reach Alfred; the token is a second lock.

### Alternative: your own domain (reachable from anywhere, including a watch on its own LTE)

Point a DNS name at the server and put Caddy in front (automatic HTTPS):

```bash
sudo apt install caddy
echo 'alfred.example.com {
  reverse_proxy 127.0.0.1:8000
}' | sudo tee /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Now Alfred is on the public internet, protected only by the token. Keep it long and secret,
and consider `IP_ALLOWLIST` in `.env`.

### Home network only

`sudo ./deploy/install.sh --lan` makes Alfred listen on `http://<server-ip>:8000`. Typing works
from any device; **speaking does not** (no HTTPS), except on the server itself.

## 3. Phone or tablet (iPhone, iPad, Android)

1. Open your Alfred address in **Safari** (iPhone/iPad) or **Chrome** (Android).
2. Tap the gear, paste the **access token**.
3. Add it to the home screen: Safari → Share → **Add to Home Screen**;
   Chrome → ⋮ → **Install app**. It now opens full-screen like any app, with Alfred's icon.
4. Open it and tap once to start (phones require one tap before using the microphone).
   From then on just say **“Alfred, …”**. Talk over him to interrupt.

Phones stop web apps from listening when they are closed or the screen is off. Hands-free works
while Alfred is open on screen (the app keeps the screen awake). For an always-listening Alfred
in a room, use an old phone or tablet on a stand, or a Raspberry Pi speaker (section 6).

## 4. Watch

Watches can't run the web app, but they can ask Alfred a question and speak the answer, using
the one-shot endpoint `POST /v1/ask`.

### Apple Watch: “Hey Siri, ask Alfred”

On your iPhone, open **Shortcuts** → **+** and add these actions:

1. **Dictate Text** (language: yours; stop listening: after pause).
2. **Get Contents of URL**
   - URL: `https://<your Alfred address>/v1/ask`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer <your access token>`
   - Request Body: **JSON**: `message` = *Dictated Text*, `conversation_id` = `latest`
3. **Get Dictionary Value**: key `reply` from *Contents of URL*.
4. **Speak Text**: *Dictionary Value*.

Name it **Ask Alfred**, and in its settings turn on **Show on Apple Watch**. Now say
“Hey Siri, ask Alfred” on the watch (or tap it on a watch face complication), then speak.
`conversation_id: latest` continues your recent conversation, so follow-ups work.

The watch reaches Alfred through your iPhone. With Tailscale this works when the phone is nearby
and Tailscale is on; to use the watch on its own LTE, use the own-domain option above.

To hear **Alfred's own voice** instead of Siri's: send `"voice": true` in the body, then
**Get Dictionary Value** `audio_b64` → **Base64 Decode** → **Play Sound**.

### Wear OS / Android without the app

Use **HTTP Shortcuts** (free, open source) or **Tasker** with the same request:
`POST https://<address>/v1/ask`, header `Authorization: Bearer <token>`, JSON body
`{"message": "<what you said>", "conversation_id": "latest"}`, then read `reply` aloud with
the app's text-to-speech action. Add the shortcut as a watch tile or Assistant routine.

## 5. Laptop or desktop

- **App:** open your Alfred address in Chrome or Edge → install icon in the address bar
  (“Install Alfred”). Hands-free while the window is open.
- **Terminal:**
  ```bash
  pip install httpx
  python clients/alfred_cli.py --server https://<address> --token <token>
  ```

## 6. Always-listening speaker (Raspberry Pi, old laptop)

Any computer with a microphone and speaker becomes an Alfred speaker:

```bash
sudo apt install -y python3-pip libportaudio2
pip install websockets sounddevice numpy
python3 clients/speaker.py --server https://<address> --token <token>
```

Say “Alfred, …” from across the room. It mutes its microphone while Alfred talks (an ordinary
speaker would let him hear himself). With a USB **speakerphone** that has echo cancellation
(e.g. Jabra Speak, Anker PowerConf), add `--barge-in` so you can interrupt him by talking.
`--list-devices` shows microphones and speakers; pick with `--input-device` / `--output-device`.

To start it at boot on a Pi, create `~/.config/systemd/user/alfred-speaker.service`:

```ini
[Unit]
Description=Alfred speaker
[Service]
Environment=ALFRED_SERVER=https://<address>
Environment=ALFRED_TOKEN=<token>
ExecStart=/usr/bin/python3 %h/Alfred/clients/speaker.py
Restart=always
[Install]
WantedBy=default.target
```

then `systemctl --user enable --now alfred-speaker && sudo loginctl enable-linger $USER`.

## 7. Your own integrations

Everything above uses the same API, documented in `docs/API.md`:

- `POST /v1/ask`: one question, one JSON answer (watches, shortcuts, automations)
- `POST /v1/chat`: streamed answer (text, optionally with audio)
- `WS /v1/voice`: continuous hands-free audio (apps, speakers)
- `POST /v1/tts`, `POST /v1/stt`: Alfred's voice and hearing on their own

## Troubleshooting

| Symptom | Fix |
|---|---|
| “Microphones only work over https://” | use the Tailscale or own-domain address, not `http://` |
| “Token rejected” | copy `ALFRED_API_TOKEN` from `/opt/alfred/.env` again |
| He never reacts to “Alfred” | check settings → server line shows `hearing: whisper (ready)`; try `WHISPER_MODEL=small` |
| He answers the TV | lower `FOLLOW_UP_WINDOW_S`, or use push-to-talk (settings → Hands-free off) |
| He interrupts himself on a speaker | don't use `--barge-in` without an echo-cancelling speakerphone |
| Answers are slow | `ALFRED_MODEL=gemma3:4b`, or a GPU; `journalctl -u ollama` shows load times |
| Anything else | `sudo journalctl -u alfred -n 100` |
