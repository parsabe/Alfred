# Alfred · android

Alfred on your Android phone or tablet: say **“Alfred, …”** and he answers aloud. A native
Kotlin app that shows Alfred's interface from **your own server** and connects the microphone
to Android's permission system. Everything Alfred does happens on the server; the app needs no
API keys and stores nothing but your server address.

> This is the `android` branch of [parsabe/Alfred](https://github.com/parsabe/Alfred).
> The server is on [`kernel`](https://github.com/parsabe/Alfred/tree/kernel), the interface it
> shows on [`web`](https://github.com/parsabe/Alfred/tree/web).

## Get the app

**Ready-made:** open the repository's **Actions** tab → **Android** → latest run →
download **alfred-android-debug** → unzip → open `app-debug.apk` on your phone (allow
“install unknown apps” for your browser or file manager when asked).

**Build it yourself:** Android Studio → *Open* this folder → ▶ Run. Or from a terminal with the
Android SDK installed:

```bash
./gradlew assembleDebug        # → app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest    # unit tests
```

## Use it

1. Install Tailscale on the phone and sign in (the same account as your server), or use your own
   domain. See [DEVICES.md](https://github.com/parsabe/Alfred/blob/docs/DEVICES.md).
2. Open **Alfred**, enter your server address, e.g. `https://alfred.your-tailnet.ts.net`.
3. Tap once to start, enter the access token in Alfred's settings (gear), allow the microphone.
4. Say “Alfred, …”. Talk over him to interrupt.

Long-press the app icon → **Change server** to point it somewhere else.

## How it works

| | |
|---|---|
| `MainActivity.kt` | full-screen WebView with Alfred's interface; grants the microphone **only to your server's origin** after Android's runtime permission; keeps the screen on while open; opens other links in the browser; clear error with Retry/Change server when the server can't be reached |
| `ServerConfig.kt` | turns what you type into a clean address and checks which pages belong to it (unit-tested) |
| `res/xml/shortcuts.xml` | the long-press “Change server” shortcut |

Requirements: Android 8.0+. The microphone needs an `https://` server address (Android, like
every browser engine, refuses it on plain `http://`); over `http://` you can still type.

## Limits and next steps

- Android stops apps from using the microphone in the background, so hands-free works while
  Alfred is on screen. A native foreground-service listener (screen off, notification shown)
  is the next step for this branch.
- The CI build is a debug APK. For a Play Store / signed release, add a keystore and a
  `release` signing config.
