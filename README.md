# Alfred

A discreet, highly capable personal assistant that lives on **your own server**. Say
**“Alfred, …”** to your phone, watch, computer or a speaker in the room, and he answers aloud.
Talk over him and he stops to listen.

Everything runs on your machine: **Gemma 3 12B** thinks, **Whisper** hears, **Piper** speaks.
No API keys, no cloud services.

![Alfred](alfred.png)

## Branches

Each part of Alfred lives on its own branch. The branches are independent and never merged.

| Branch | What it is | Get it |
|---|---|---|
| [**kernel**](https://github.com/parsabe/Alfred/tree/kernel) | **The server.** Alfred's mind, hearing, voice and the API every device talks to. Ubuntu installer included. | `git clone --branch kernel https://github.com/parsabe/Alfred.git` |
| [**web**](https://github.com/parsabe/Alfred/tree/web) | **The interface.** The glowing ribbon, live voice conversation, installable on any phone or computer. The server serves it automatically. | fetched by the kernel installer |
| [**android**](https://github.com/parsabe/Alfred/tree/android) | **Android app** (Kotlin) for phones and tablets. | APK from **Actions → Android** |
| [**linux**](https://github.com/parsabe/Alfred/tree/linux) | **Linux client**, compiled to one program: hands-free listening, app window, terminal chat; also turns a Raspberry Pi into an Alfred speaker. | binary from **Actions → Linux**, or `./build.sh` |
| [**windows**](https://github.com/parsabe/Alfred/tree/windows) | **Windows client**: `Alfred.exe` and an installer with Start-menu entries. | installer from **Actions → Windows**, or `.\build.ps1` |
| [**docs**](https://github.com/parsabe/Alfred/tree/docs) | **Documentation**: devices guide, API reference, design, requirements and progress. | [read it](https://github.com/parsabe/Alfred/tree/docs) |

## How they fit together

```
   android app ─┐
   iPhone / web ┤                 ┌──────────── kernel (your Ubuntu server) ─────────────┐
   Apple Watch ─┼── https:// ────▶│  Gemma 3 12B · Whisper · Piper · conversations       │
   windows app ─┤   (Tailscale)   │  serves the web interface (web branch) at /          │
   linux app ───┘                 └──────────────────────────────────────────────────────┘
```

## Start here

1. **Install the server** (Ubuntu, 12 GB RAM is enough):
   ```bash
   git clone --branch kernel https://github.com/parsabe/Alfred.git && cd Alfred
   sudo ./deploy/install.sh --tailscale
   ```
   It prints your Alfred address and access token.
2. **Connect your devices**: phone, watch, PC, speaker.
   Step by step: [DEVICES.md](https://github.com/parsabe/Alfred/blob/docs/DEVICES.md).

Building something of your own on top of Alfred? The API is in
[API.md](https://github.com/parsabe/Alfred/blob/docs/API.md).
