# Alfred · web

The ribbon interface: Alfred's face in any browser, and the app you install on your phone,
tablet or computer. Plain HTML/CSS/JS, no build step.

> This is the `web` branch of [parsabe/Alfred](https://github.com/parsabe/Alfred). The server
> ([`kernel`](https://github.com/parsabe/Alfred/tree/kernel)) fetches this branch and serves it
> at `/`, so normally you never deploy it yourself: `deploy/install.sh` and `make web` do it.

![The ribbon](icons/icon-512.png)

## What it does

- **Hands-free:** streams the microphone to Alfred's server (`WS /v1/voice`), which listens for
  “Alfred, …”, answers, and handles interruptions. Tap or press Space to talk without his name.
- **The ribbon** (`ribbon.js`) shows who is talking: a resting hairline when he's asleep, following
  your voice while you speak, undulating while he thinks, swelling with his voice as he speaks.
- **Plays his voice** with ducking/interruption (`audio.js`); falls back to the browser's own voice
  if the server has none.
- **Installable** (`manifest.webmanifest`, `sw.js`): Add to Home Screen on iPhone/Android,
  Install in Chrome/Edge. Keeps the screen awake while listening.
- Captions, typing (`/`), settings (`,`): server, token (kept on the device), hands-free,
  voice, captions, keep screen on.

| File | |
|---|---|
| `index.html`, `style.css` | page and settings sheet |
| `app.js` | connection, state machine, input, settings |
| `audio.js` | microphone capture, playback, ducking, browser-voice fallback |
| `capture-worklet.js` | audio thread: microphone → 16 kHz 16-bit PCM frames |
| `ribbon.js` | the ribbon renderer (canvas, additive layers, bloom) |
| `sw.js`, `manifest.webmanifest`, `icons/` | installable app |

## Hosting it somewhere else

It works from any static host as long as the page is `https://` (browsers only allow the
microphone on secure pages):

1. Serve these files (`python3 -m http.server`, nginx, GitHub Pages, …).
2. On the server, allow that origin: `CORS_ORIGINS=https://your-host` in `.env`.
3. In the app's settings, set **Server** to your Alfred address.

## Previews without a server

`index.html?demo=speaking` (also `idle`, `listening`, `thinking`) animates the ribbon with a
synthetic voice level, no server needed.

The end-to-end test for this interface lives in the kernel branch (`make e2e`), because it
needs the server.
