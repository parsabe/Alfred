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
| Android phone or tablet | the **Alfred app** ([`android`](https://github.com/parsabe/Alfred/tree/android) branch), or the web app | yes, while the app is open |
| iPhone / iPad | the Alfred web app, added to the home screen | yes, while the app is open |
| Apple Watch | a Shortcut (“Hey Siri, ask Alfred”) | via Siri |
| Wear OS watch | an HTTP-shortcut app or Tasker | via the shortcut/Assistant |
| Windows PC | **Alfred.exe** ([`windows`](https://github.com/parsabe/Alfred/tree/windows) branch): app window, hands-free, terminal | yes |
| Linux computer | **alfred** ([`linux`](https://github.com/parsabe/Alfred/tree/linux) branch): app window, hands-free, terminal | yes |
| Mac | the web app installed from Chrome/Edge | yes, while the app is open |
| Raspberry Pi / old computer with a mic and speaker | `alfred listen` from the [`linux`](https://github.com/parsabe/Alfred/tree/linux) branch | yes, always |
| Anything else (home automation, scripts) | the HTTP API ([API.md](API.md)) | — |

---

## 1. Install the server

On the Ubuntu server:

```bash
git clone --branch kernel https://github.com/parsabe/Alfred.git && cd Alfred
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

**With aaPanel / BaoTa or an existing nginx** (domain already pointing at the server):

```bash
sudo ./deploy/install.sh --domain alfred.example.com
```

The installer keeps Alfred private on `127.0.0.1:8000`, writes the nginx settings he needs to
`/opt/alfred/deploy/nginx-alfred.example.com.conf`, and prints the panel steps: issue a Let's
Encrypt certificate for the site, add a reverse proxy to `http://127.0.0.1:8000`, and make sure
the proxy configuration has the WebSocket `Upgrade`/`Connection` headers, `proxy_buffering off`
and `proxy_read_timeout 3600s` (otherwise hands-free voice disconnects and replies arrive all at
once instead of word by word).

**Without a panel**, Caddy does HTTPS automatically:

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

## 3. Phone or tablet

### Android: the Alfred app

1. Get the APK: repository → **Actions** → **Android** → latest run → download
   **alfred-android-debug** → unzip → open `app-debug.apk` on the phone (allow installing
   unknown apps for your browser or file manager when asked). Or build it from the
   [`android`](https://github.com/parsabe/Alfred/tree/android) branch with Android Studio.
2. Install Tailscale on the phone and sign in.
3. Open **Alfred**, enter your Alfred address, tap once, enter the access token (gear), allow the
   microphone. Say “Alfred, …”.

Long-press the app icon → **Change server** to switch servers.

### iPhone, iPad (or Android without the app): the web app

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

**Windows:** download **alfred-windows** from the repository's **Actions** → **Windows** run, run
**Alfred-Setup.exe**, enter your address and token. Start menu → **Alfred** (app window) or
**Alfred (hands-free)**. Details: [`windows`](https://github.com/parsabe/Alfred/tree/windows) branch.

**Linux:** download **alfred-linux-x86_64** from **Actions** → **Linux**, unpack,
`sudo apt install libportaudio2 && ./install.sh`. Then `alfred` (hands-free), `alfred open`
(app window), `alfred chat` (terminal). Details: [`linux`](https://github.com/parsabe/Alfred/tree/linux) branch.

**Mac (or any computer):** open your Alfred address in Chrome or Edge → install icon in the
address bar (“Install Alfred”). Hands-free while the window is open.

## 6. Always-listening speaker (Raspberry Pi, old laptop)

Any Linux computer with a microphone and speaker becomes an Alfred speaker, using the client
from the [`linux`](https://github.com/parsabe/Alfred/tree/linux) branch:

```bash
sudo apt install -y python3 python3-venv libportaudio2 git
git clone --branch linux https://github.com/parsabe/Alfred.git alfred-linux && cd alfred-linux
./build.sh && ./install.sh --service      # asks for address + token, then listens at boot
sudo loginctl enable-linger $USER         # start without anyone logging in
```

Say “Alfred, …” from across the room. It mutes its microphone while Alfred talks (an ordinary
speaker would let him hear himself). With a USB **speakerphone** that has echo cancellation
(e.g. Jabra Speak, Anker PowerConf), run `alfred listen --barge-in --save` once so you can
interrupt him by talking. `alfred devices` lists microphones and speakers; pick with
`alfred listen --input-device N --output-device N --save`.

On a Raspberry Pi (ARM), build on the Pi itself with `./build.sh`; the ready-made download is for
x86-64 PCs.

## 7. Your own integrations

Everything above uses the same API, documented in [API.md](API.md):

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
| Install stops: `uv: Permission denied` | update (`git pull`) and run the installer again; fixed on 2026-11-03 |
| Voice disconnects / replies arrive all at once behind nginx | add the nginx lines from `/opt/alfred/deploy/nginx-<domain>.conf` |
| Firefox: “The page isn't redirecting properly” | a redirect loop, usually Cloudflare SSL set to “Flexible”: set it to **Full (strict)** |
| Anything else | `sudo /opt/alfred/deploy/doctor.sh your-domain` checks everything and names the fix |
