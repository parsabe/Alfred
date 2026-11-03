// End-to-end check of hands-free Alfred in headless Chromium.
//
// Starts the demo server with a scripted recogniser and tone "voice", gives Chromium a fake
// microphone that "speaks" for 1.4 s every 5.4 s, and verifies, through the real page, worklet,
// WebSocket, server VAD and playback:
//   1. a remark not addressed to Alfred is ignored (he stays asleep);
//   2. "Alfred, is the car ready?" wakes him: thinking → speaking;
//   3. talking over him interrupts him (duck, interrupt) and he answers the new sentence;
//   4. after he finishes, he listens for a follow-up without the wake word, then sleeps;
//   5. typing still works.
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
const SCRIPT = [
  "The weather is nice today.",          // not addressed to Alfred: ignored
  "Alfred, is the car ready?",            // wakes him
  "Actually, never mind. What time is it?", // spoken over him: interrupts
  "",                                      // silence / nothing recognised
  "",
];

function micWav(path) {
  // 1.0 s silence, 1.4 s "speech", 3.0 s silence; Chromium loops the file
  const rate = 16000;
  const samples = [];
  for (const [secs, voiced] of [[1.0, false], [1.4, true], [3.0, false]]) {
    for (let i = 0; i < secs * rate; i++) {
      const t = i / rate;
      const env = voiced ? 0.55 + 0.45 * Math.abs(Math.sin(2 * Math.PI * 3.5 * t)) : 0;
      const f0 = 150 + 30 * Math.sin(t * 7);
      const voice = Math.sin(2 * Math.PI * f0 * t) + 0.5 * Math.sin(4 * Math.PI * f0 * t) + 0.3 * Math.sin(6 * Math.PI * f0 * t);
      samples.push(env * 0.3 * voice + (Math.random() - 0.5) * 0.002);
    }
  }
  const data = Buffer.alloc(44 + samples.length * 2);
  data.write("RIFF", 0); data.writeUInt32LE(36 + samples.length * 2, 4); data.write("WAVE", 8);
  data.write("fmt ", 12); data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24); data.writeUInt32LE(rate * 2, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34);
  data.write("data", 36); data.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((s, i) => data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, s)) * 32767), 44 + i * 2));
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

const server = spawn(
  "uv",
  ["run", "python", "-m", "scripts.demo", "--fake-voice", "--port", String(PORT), "--script", SCRIPT.join("|")],
  { stdio: ["ignore", "inherit", "inherit"] },
);
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
  }, TOKEN);
  const page = await context.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  page.on("console", (m) => m.type() === "error" && console.error("console:", m.text()));
  await page.goto(BASE);

  const modes = () => page.evaluate(() => window.alfred.history.join(" → "));
  const waitMode = (m, timeout = 20000) =>
    page.waitForFunction((want) => window.alfred.mode === want, m, { timeout });

  console.log("start");
  await page.click("#stage", { position: { x: 200, y: 200 } }); // "Tap to start Alfred"
  await page.waitForFunction(() => window.alfred.connected, null, { timeout: 10000 });
  await page.evaluate(() => window.alfred.history.splice(0));
  check(await page.evaluate(() => window.alfred.mode) === "idle", "connected and asleep, waiting for “Alfred”");

  console.log("1–2. ignore a remark, wake on his name");
  await waitMode("thinking", 20000);
  check((await page.textContent("#you")) === "Alfred, is the car ready?", "woke on “Alfred, is the car ready?”");
  check(!(await modes()).includes("listening"), `the earlier remark was ignored (${await modes()})`);
  await waitMode("speaking");
  await page.waitForTimeout(500);
  await page.screenshot({ path: join(shots, "speaking.png") });

  console.log("3. talking over him");
  await page.waitForFunction(() => document.querySelector("#you").textContent.startsWith("Actually"), null, { timeout: 20000 });
  check(true, "his speech was interrupted and the new sentence heard");
  await waitMode("speaking");

  console.log("4. follow-up window, then sleep");
  await waitMode("listening", 30000);
  await waitMode("idle", 30000);
  const flow = await modes();
  check(/thinking → speaking → listening → thinking → speaking → listening → idle/.test(flow),
    `full hands-free flow: ${flow}`);

  console.log("5. typing");
  await page.keyboard.press("/");
  await page.fill("#type-input", "Good evening");
  await page.keyboard.press("Enter");
  await waitMode("speaking");
  check((await page.textContent("#alfred")).includes("Good evening"), "typed message answered");

  console.log(`\nAll good. Screenshots: ${shots}`);
} catch (err) {
  console.error(err.message);
  try { console.error("modes:", await browser?.contexts()[0]?.pages()[0]?.evaluate(() => window.alfred.history.join(" → "))); } catch {}
  process.exitCode = 1;
} finally {
  await browser?.close();
  server.kill();
}
