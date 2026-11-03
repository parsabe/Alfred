// Alfred's live voice interface.
//
// Hands-free (default): the microphone streams to Alfred's server, which listens for
// "Alfred, …" and answers aloud. Talk over him to interrupt. Tap (or Space) to talk without
// saying his name. Everything is decided on the server; this page just carries audio both ways
// and draws the ribbon.

import { Ribbon } from "./ribbon.js";
import { AudioOut, BrowserVoice, Microphone, audioContext } from "./audio.js";

const $ = (id) => document.getElementById(id);

// --- preferences (per browser; storage may be unavailable) ------------------------------------
const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem(`alfred.${key}`); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`alfred.${key}`, JSON.stringify(value)); } catch {}
  },
};

const opts = {
  server: prefs.get("server", ""),          // empty = the server this page came from
  token: prefs.get("token", ""),
  handsFree: prefs.get("handsFree", true),
  voice: prefs.get("voice", true),
  captions: prefs.get("captions", true),
  awake: prefs.get("awake", true),
};
let conversationId = prefs.get("conversation", null);

function serverBase() {
  return (opts.server || location.origin).replace(/\/+$/, "");
}

// --- ribbon ---------------------------------------------------------------------------------
const ribbon = new Ribbon($("ribbon"), {
  reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
});
ribbon.start();

const demo = new URLSearchParams(location.search).get("demo");
if (demo) {
  // ?demo=idle|listening|thinking|speaking : animation preview, no server needed
  ribbon.setState(demo);
  ribbon.levelSource = () => {
    const t = performance.now() / 1000;
    return demo === "speaking" || demo === "listening"
      ? 0.45 + 0.4 * Math.abs(Math.sin(t * 3.1)) * Math.abs(Math.sin(t * 1.3 + 1))
      : 0;
  };
  $("hint").classList.add("gone");
}

// --- state ----------------------------------------------------------------------------------
const out = new AudioOut();
const browserVoice = new BrowserVoice();
let ws = null;
let mic = null;
let mode = "idle";
let started = false;
let reconnectDelay = 1000;
let server = { tts: null, stt: null, wakeWord: "Alfred" };
let turn = { text: "", gotAudio: false, done: false };
const history = []; // recent modes, for debugging and tests

function setStatus(text, isError = false) {
  const el = $("status");
  el.textContent = text || "";
  el.classList.toggle("error", isError);
}

function idleHint() {
  if (!started) return "Tap to start Alfred";
  return opts.handsFree ? `Say “${server.wakeWord}, …”` : "Tap to speak";
}

function setMode(next) {
  if (next !== mode || !history.length) history.push(next);
  if (history.length > 50) history.shift();
  mode = next;
  document.body.dataset.mode = next;
  if (demo) return;
  ribbon.setState(next);
  ribbon.levelSource =
    next === "listening" ? () => mic?.level() ?? 0
    : next === "speaking" ? () => (out.playing ? out.level() : browserVoice.level())
    : next === "idle" && opts.handsFree ? () => (mic?.level() ?? 0) * 0.5  // he hears you, faintly
    : null;
  if (!ribbon.levelSource) ribbon.setLevel(0);
  if (next === "idle") setStatus(idleHint());
  else if (next === "listening") setStatus("listening");
  else setStatus("");
}

function caption(id, text) {
  const el = $(id);
  el.textContent = text.length > 420 ? "…" + text.slice(-420) : text;
}

// --- connection ----------------------------------------------------------------------------
function send(obj) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connect() {
  if (!opts.token) return openSettings("Enter the access token from your server's .env file.");
  const url = serverBase().replace(/^http/, "ws") + "/v1/voice";
  const socket = new WebSocket(url, ["bearer", opts.token]);
  ws = socket;
  socket.binaryType = "arraybuffer";
  socket.onopen = () => {
    reconnectDelay = 1000;
    socket.send(JSON.stringify({
      type: "hello",
      mode: opts.handsFree ? "wake" : "ptt",
      conversation_id: conversationId,
      sample_rate: 16000,
    }));
  };
  socket.onmessage = (e) => onEvent(JSON.parse(e.data));
  socket.onclose = (e) => {
    if (ws !== socket) return;
    ws = null;
    out.stop();
    if (e.code === 4401) {
      started = false;
      setMode("idle");
      return openSettings("The server didn't accept that token.");
    }
    setStatus("reconnecting…", true);
    setTimeout(() => { if (started && !ws) connect(); }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  };
}

function onEvent(ev) {
  if (ev.conversation_id && ev.conversation_id !== conversationId) {
    conversationId = ev.conversation_id;
    prefs.set("conversation", conversationId);
  }
  const d = ev.data || {};
  switch (ev.type) {
    case "ready":
      server = { tts: d.tts, stt: d.stt, wakeWord: d.wake_word || "Alfred" };
      if (!d.stt) setStatus("Speech recognition is off on the server; type with /.", true);
      break;
    case "state":
      setMode(d.value);
      break;
    case "transcript":
      caption("you", d.text);
      caption("alfred", "");
      turn = { text: "", gotAudio: false, done: false };
      break;
    case "token":
      turn.text += d.text;
      caption("alfred", turn.text);
      break;
    case "audio":
      turn.gotAudio = true;
      if (opts.voice) out.enqueue(d.data_b64);
      break;
    case "duck":
      out.duck(true);
      break;
    case "unduck":
      out.duck(false);
      break;
    case "interrupt":
      out.stop();
      browserVoice.stop();
      break;
    case "error":
      setStatus(d.message, true);
      break;
    case "done":
      turn.done = true;
      finishPlayback(turn);
      break;
  }
}

async function finishPlayback(current) {
  if (current.gotAudio && opts.voice) {
    await out.drained();
  } else if (!current.gotAudio && opts.voice && current.text && !server.tts) {
    await browserVoice.speak(current.text); // no voice on the server: the browser stands in
  }
  if (current === turn) send({ type: "playback", state: "ended" });
}

// --- starting ------------------------------------------------------------------------------
let wakeLock = null;
async function keepAwake() {
  if (!opts.awake || !("wakeLock" in navigator) || document.visibilityState !== "visible") return;
  try { wakeLock = await navigator.wakeLock.request("screen"); } catch {}
}
document.addEventListener("visibilitychange", () => { if (started) keepAwake(); });

async function start() {
  if (started) return;
  if (!opts.token) return openSettings("Enter the access token from your server's .env file.");
  audioContext(); // browsers only allow audio after a user gesture
  $("hint").classList.add("gone");
  mic = new Microphone((frame) => { if (ws?.readyState === WebSocket.OPEN) ws.send(frame); });
  try {
    await mic.start();
  } catch (err) {
    console.warn(err);
    mic = null;
    setStatus(location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1"
      ? "Microphone access is needed to talk to Alfred."
      : "Microphones only work over https:// (see docs/DEVICES.md). You can still type with /.", true);
  }
  started = true;
  keepAwake();
  connect();
  setMode("idle");
}

function stop() {
  started = false;
  ws?.close();
  ws = null;
  mic?.stop();
  mic = null;
  out.stop();
  browserVoice.stop();
  wakeLock?.release?.();
  setMode("idle");
}

// --- input ---------------------------------------------------------------------------------
function onTap() {
  if (demo) return;
  if (!started) return start();
  audioContext();
  send({ type: "wake" }); // talk without the name; also interrupts him if he's speaking
  if (mode === "speaking") { out.stop(); browserVoice.stop(); }
}

$("stage").addEventListener("click", onTap);

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, dialog")) {
    if (e.key === "Escape" && e.target.id === "type-input") toggleTyping(false);
    return;
  }
  if (e.code === "Space") { e.preventDefault(); onTap(); }
  else if (e.key === "Escape") { send({ type: "stop" }); out.stop(); browserVoice.stop(); }
  else if (e.key === "/") { e.preventDefault(); toggleTyping(true); }
  else if (e.key === ",") openSettings();
});

function toggleTyping(show) {
  const form = $("type-form");
  form.hidden = show === undefined ? !form.hidden : !show;
  if (!form.hidden) $("type-input").focus();
}

$("keyboard-btn").addEventListener("click", () => toggleTyping());
$("type-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("type-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  if (!started) await start();
  send({ type: "text", message: text });
});

// --- settings -------------------------------------------------------------------------------
const dialog = $("settings");

function openSettings(message) {
  $("server").value = opts.server;
  $("server").placeholder = location.origin;
  $("token").value = opts.token;
  $("opt-handsfree").checked = opts.handsFree;
  $("opt-voice").checked = opts.voice;
  $("opt-captions").checked = opts.captions;
  $("opt-awake").checked = opts.awake;
  const info = $("server-info");
  info.textContent = message || "";
  info.classList.toggle("bad", Boolean(message));
  if (!dialog.open) dialog.showModal();
  if (!message) checkServer();
}

async function checkServer() {
  const info = $("server-info");
  if (!opts.token) return;
  try {
    const res = await fetch(`${serverBase()}/health`, { headers: { Authorization: `Bearer ${opts.token}` } });
    const health = await res.json();
    if (!health.llm) throw Object.assign(new Error(), { code: 401 });
    const model = health.llm.configured ? health.llm.model : "no model configured";
    const hear = health.voice?.stt ? `hearing: ${health.voice.stt} (${health.voice.stt_status})` : "hearing: off";
    const speak = health.voice?.tts ? `voice: ${health.voice.tts} (${health.voice.tts_status})` : "voice: browser";
    info.textContent = `Connected · ${model} · ${hear} · ${speak}`;
    info.classList.toggle("bad", !health.llm.configured);
  } catch (err) {
    info.textContent = err.code === 401 ? "Token rejected by the server." : "Can't reach Alfred's server.";
    info.classList.add("bad");
  }
}

$("settings-btn").addEventListener("click", () => openSettings());

function applySettings() {
  const before = { server: opts.server, token: opts.token, handsFree: opts.handsFree };
  opts.server = $("server").value.trim();
  opts.token = $("token").value.trim();
  opts.handsFree = $("opt-handsfree").checked;
  opts.voice = $("opt-voice").checked;
  opts.captions = $("opt-captions").checked;
  opts.awake = $("opt-awake").checked;
  for (const key of Object.keys(opts)) prefs.set(key, opts[key]);
  out.muted = !opts.voice;
  document.body.classList.toggle("no-captions", !opts.captions);
  if (before.handsFree !== opts.handsFree) send({ type: "mode", mode: opts.handsFree ? "wake" : "ptt" });
  if (started && (before.server !== opts.server || before.token !== opts.token)) {
    const s = ws; ws = null; s?.close(); connect();
  }
  if (mode === "idle") setStatus(idleHint());
}

for (const id of ["server", "token", "opt-handsfree", "opt-voice", "opt-captions", "opt-awake"]) {
  $(id).addEventListener("change", () => { applySettings(); if (id === "token" || id === "server") checkServer(); });
}
dialog.addEventListener("close", applySettings);

$("new-conversation").addEventListener("click", () => {
  send({ type: "stop" });
  conversationId = null;
  prefs.set("conversation", null);
  caption("you", "");
  caption("alfred", "");
  if (started) { const s = ws; ws = null; s?.close(); connect(); }
  dialog.close();
});
$("stop-listening").addEventListener("click", () => { stop(); dialog.close(); });

// --- boot -----------------------------------------------------------------------------------
document.body.classList.toggle("no-captions", !opts.captions);
out.muted = !opts.voice;
setMode("idle");
$("hint").textContent = opts.token ? "Tap to start Alfred" : "Tap to set up";
if ("serviceWorker" in navigator && !demo) navigator.serviceWorker.register("sw.js").catch(() => {});

window.alfred = {
  get mode() { return mode; },
  get conversationId() { return conversationId; },
  get connected() { return ws?.readyState === WebSocket.OPEN; },
  history,
  out,
};
