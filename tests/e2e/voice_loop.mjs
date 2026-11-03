// End-to-end check of the live voice interface in headless Chromium.
//
// Starts the demo server (stand-in brain, tone voice), feeds Chromium a fake microphone that
// "speaks" (a syllable-modulated tone) and then goes quiet, and verifies:
//   1. a typed message → thinking → speaking (server audio plays) → idle, captions filled;
//   2. a spoken message → listening → server transcription → speaking → listening again
//      (conversation mode);
//   3. tapping while Alfred speaks interrupts him.
//
// Run: make e2e   (needs Node and Playwright with Chromium)

import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const PORT = 8799;
const BASE = `http://127.0.0.1:${PORT}`;
const TOKEN = "demo-token-not-secret-0123456789abcdef";
const shots = process.env.E2E_SHOTS || mkdtempSync(join(tmpdir(), "alfred-e2e-"));

function micWav(path) {
  // 1.0 s silence, 1.4 s "speech", 3.0 s silence (Chromium loops the file)
  const rate = 16000;
  const parts = [[1.0, false], [1.4, true], [3.0, false]];
  const samples = [];
  for (const [secs, voiced] of parts) {
    for (let i = 0; i < secs * rate; i++) {
      const t = i / rate;
      const env = voiced ? 0.5 + 0.5 * Math.abs(Math.sin(2 * Math.PI * 3.5 * t)) : 0;
      const noise = (Math.random() - 0.5) * 0.002;
      samples.push(env * 0.5 * Math.sin(2 * Math.PI * (180 + 40 * Math.sin(t * 9)) * t) + noise);
    }
  }
  const data = Buffer.alloc(44 + samples.length * 2);
  data.write("RIFF", 0); data.writeUInt32LE(36 + samples.length * 2, 4); data.write("WAVE", 8);
  data.write("fmt ", 12); data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24); data.writeUInt32LE(rate * 2, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34);
  data.write("data", 36); data.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((s, i) => data.writeInt16LE(Math.max(-1, Math.min(1, s)) * 32767, 44 + i * 2));
  writeFileSync(path, data);
}

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`${BASE}/health`)).ok) return; } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("demo server did not start");
}

function check(cond, message) {
  if (!cond) throw new Error(`FAILED: ${message}`);
  console.log(`  ✓ ${message}`);
}

const server = spawn("uv", ["run", "python", "-m", "scripts.demo", "--tone", "--port", String(PORT)], {
  stdio: ["ignore", "inherit", "inherit"],
});
let browser;
try {
  await waitForServer();
  const wav = join(shots, "mic.wav");
  micWav(wav);
  browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${wav}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const context = await browser.newContext({ viewport: { width: 1260, height: 680 } });
  await context.grantPermissions(["microphone"], { origin: BASE });
  await context.addInitScript((token) => {
    localStorage.setItem("alfred.token", JSON.stringify(token));
    localStorage.setItem("alfred.stt", JSON.stringify("server"));
  }, TOKEN);
  const page = await context.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.goto(BASE);
  const modes = () => page.evaluate(() => [...window.alfred.history]);
  const resetModes = () => page.evaluate(() => window.alfred.history.splice(0));
  const waitMode = (m, timeout = 15000) =>
    page.waitForFunction((want) => window.alfred.mode === want, m, { timeout });

  console.log("1. typed turn");
  await resetModes();
  await page.keyboard.press("/");
  await page.fill("#type-input", "Is the car ready?");
  await page.keyboard.press("Enter");
  await waitMode("speaking");
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(shots, "speaking.png") });
  check((await page.textContent("#alfred")).includes("Is the car ready"), "Alfred's reply is captioned");
  await waitMode("idle", 20000);
  const typed = await modes();
  check(JSON.stringify(typed.slice(-3)) === JSON.stringify(["thinking", "speaking", "idle"]),
    `typed turn went thinking → speaking → idle (${typed.join(" → ")})`);

  console.log("2. spoken turn, conversation mode");
  await resetModes();
  await page.click("#stage", { position: { x: 200, y: 200 } });
  await waitMode("listening");
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(shots, "listening.png") });
  await waitMode("speaking", 20000);
  check((await page.textContent("#you")) === "Good evening, Alfred.", "speech was transcribed by the server");
  await waitMode("listening", 20000);
  const spoken = await modes();
  check(JSON.stringify(spoken.slice(0, 4)) === JSON.stringify(["listening", "thinking", "speaking", "listening"]),
    `spoken turn went listening → thinking → speaking → listening (${spoken.join(" → ")})`);

  console.log("3. interrupting Alfred");
  await page.keyboard.press("Escape");
  await waitMode("idle");
  await page.keyboard.press("/");
  await page.fill("#type-input", "Tell me a long story");
  await page.keyboard.press("Enter");
  await waitMode("speaking");
  await page.click("#stage", { position: { x: 200, y: 200 } });
  await waitMode("listening");
  check(!(await page.evaluate(() => window.alfred.out.playing)), "tapping stopped Alfred's audio and listened");

  console.log(`\nAll good. Screenshots: ${shots}`);
} catch (err) {
  console.error(err.message);
  process.exitCode = 1;
} finally {
  await browser?.close();
  server.kill();
}
