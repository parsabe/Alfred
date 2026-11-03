// Alfred's live voice interface: tap to talk, Alfred answers aloud, the ribbon shows who is talking.
//
// idle ──tap──▶ listening ──speech──▶ thinking ──audio──▶ speaking ──done──▶ listening (conversation
// mode) or idle. Tapping while Alfred thinks or speaks interrupts him and listens again.

import { Ribbon } from "./ribbon.js";
import {
  AudioOut, BrowserRecognizer, BrowserVoice, Mic, ServerRecognizer, audioContext, hasBrowserSTT,
} from "./audio.js";

const $ = (id) => document.getElementById(id);

// --- per-browser preferences (best effort: storage may be unavailable) ----------------------
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
  token: prefs.get("token", ""),
  continuous: prefs.get("continuous", true),
  voice: prefs.get("voice", true),
  captions: prefs.get("captions", true),
  stt: prefs.get("stt", "auto"),
  lang: prefs.get("lang", ""),
};
let conversationId = prefs.get("conversation", null);

// --- ribbon -----------------------------------------------------------------------------
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

// --- state --------------------------------------------------------------------------------
const out = new AudioOut();
const browserVoice = new BrowserVoice();
const mic = new Mic();
let server = { ok: false, voice: false, stt: false, llm: false };
let mode = "idle";
let turn = null;
let recognizer = null;
let spokenInput = true;

function setStatus(text, isError = false) {
  const el = $("status");
  el.textContent = text || "";
  el.classList.toggle("error", isError);
}

const history = []; // recent modes, for debugging and tests

function setMode(next) {
  if (next !== mode || !history.length) history.push(next);
  if (history.length > 50) history.shift();
  mode = next;
  if (demo) return;
  ribbon.setState(next);
  ribbon.levelSource =
    next === "listening" ? () => mic.level()
    : next === "speaking" ? () => (out.playing ? out.level() : browserVoice.level())
    : null;
  if (!ribbon.levelSource) ribbon.setLevel(0);
  document.body.dataset.mode = next;
}

function caption(id, text) {
  const el = $(id);
  el.textContent = text.length > 420 ? "…" + text.slice(-420) : text;
}

// --- server ---------------------------------------------------------------------------------
async function api(path, init = {}) {
  const res = await fetch(path, {
    ...init,
    headers: { ...(init.headers || {}), Authorization: `Bearer ${opts.token}` },
  });
  if (res.status === 401) {
    const err = new Error("Your access token was not accepted.");
    err.code = 401;
    throw err;
  }
  return res;
}

async function checkServer() {
  const info = $("server-info");
  if (!opts.token) {
    server = { ok: false };
    info.textContent = "Enter the ALFRED_API_TOKEN from the server's .env file.";
    info.classList.add("bad");
    return;
  }
  try {
    const health = await (await api("/health")).json();
    if (!health.llm) throw Object.assign(new Error("Your access token was not accepted."), { code: 401 });
    server = {
      ok: true,
      llm: health.llm.configured,
      voice: Boolean(health.voice?.tts),
      stt: Boolean(health.voice?.stt),
    };
    const model = health.llm.configured ? `${health.llm.model} (${health.llm.provider})` : "no model configured";
    const voice = server.voice ? "Alfred's voice" : BrowserVoice.available() ? "browser voice (no Fish Audio key on server)" : "no voice";
    info.textContent = `Connected · ${model} · ${voice}`;
    info.classList.toggle("bad", !health.llm.configured);
  } catch (err) {
    server = { ok: false };
    info.textContent = err.code === 401 ? "Token rejected by the server." : "Can't reach Alfred's server.";
    info.classList.add("bad");
  }
}

async function transcribe(blob, lang) {
  const form = new FormData();
  form.append("audio", blob, "speech");
  if (lang) form.append("language", lang.split("-")[0]);
  const res = await api("/v1/stt", { method: "POST", body: form });
  if (!res.ok) throw new Error("Alfred couldn't transcribe that.");
  return (await res.json()).text;
}

async function* sse(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value.replace(/\r\n/g, "\n");
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5)).join("\n");
      if (data.trim()) yield JSON.parse(data);
    }
  }
}

// --- listening ---------------------------------------------------------------------------
async function listen() {
  stopListening();
  spokenInput = true;
  setStatus("");
  caption("you", "");
  try {
    await mic.open();
  } catch {
    setMode("idle");
    setStatus("Microphone access is needed to talk to Alfred.", true);
    return;
  }
  setMode("listening");
  const useServer =
    opts.stt === "server" || (opts.stt === "auto" && !hasBrowserSTT);
  if (useServer && !server.stt) {
    setMode("idle");
    setStatus(hasBrowserSTT ? "Server speech recognition isn't configured." : "This browser can't recognise speech. Type with /.", true);
    return;
  }
  const handlers = {
    lang: opts.lang,
    onInterim: (text) => caption("you", text),
    onFinal: (text) => send(text),
    onEnd: () => {
      if (mode !== "listening") return;
      setMode("idle");
      setStatus("tap to speak");
    },
    onError: (message) => { setMode("idle"); setStatus(message, true); },
  };
  recognizer = useServer
    ? new ServerRecognizer({ mic, transcribe, ...handlers })
    : new BrowserRecognizer(handlers);
  try {
    recognizer.start();
  } catch (err) {
    setMode("idle");
    setStatus("Couldn't start listening.", true);
  }
}

function stopListening() {
  recognizer?.stop();
  recognizer = null;
}

// --- a turn -------------------------------------------------------------------------------
function interrupt() {
  stopListening();
  turn?.abort.abort();
  turn = null;
  out.stop();
  browserVoice.stop();
}

async function send(text) {
  stopListening();
  if (!text.trim()) return;
  if (!opts.token) return openSettings();
  interrupt();
  caption("you", text);
  caption("alfred", "");
  setStatus("");
  setMode("thinking");

  const current = (turn = { abort: new AbortController(), text: "", gotAudio: false, fallbackVoice: false });
  const wantVoice = opts.voice;
  try {
    const res = await api("/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        conversation_id: conversationId,
        output_mode: wantVoice ? "voice" : "text",
      }),
      signal: current.abort.signal,
    });
    if (res.status === 404 && conversationId) {
      conversationId = null;            // conversation was deleted on the server: start afresh
      prefs.set("conversation", null);
      turn = null;
      return send(text);
    }
    if (!res.ok) throw new Error(`Alfred's server answered ${res.status}.`);

    for await (const ev of sse(res)) {
      if (turn !== current) return;
      if (ev.conversation_id && ev.conversation_id !== conversationId) {
        conversationId = ev.conversation_id;
        prefs.set("conversation", conversationId);
      }
      switch (ev.type) {
        case "token":
          current.text += ev.data.text;
          caption("alfred", current.text);
          break;
        case "audio":
          current.gotAudio = true;
          if (mode !== "speaking") setMode("speaking");
          out.enqueue(ev.data.data_b64);
          break;
        case "error":
          if (String(ev.data.code).startsWith("voice")) current.fallbackVoice = true;
          else setStatus(ev.data.message, true);
          break;
        case "state":
          if (ev.data.value === "thinking" && mode !== "speaking") setMode("thinking");
          break;
      }
    }

    if (turn !== current) return;
    if (wantVoice && current.gotAudio) {
      await out.drained();
    } else if (wantVoice && current.text && BrowserVoice.available()) {
      setMode("speaking");
      await browserVoice.speak(current.text, opts.lang);
    }
    if (turn !== current) return;
    turn = null;
    if (opts.continuous && spokenInput && current.text) listen();
    else setMode("idle");
  } catch (err) {
    if (err.name === "AbortError" || turn !== current) return;
    turn = null;
    setMode("idle");
    setStatus(err.message || "Something went wrong.", true);
    if (err.code === 401) openSettings();
  }
}

// --- input --------------------------------------------------------------------------------
function onTap() {
  if (demo) return;
  audioContext(); // browsers only allow audio after a user gesture
  $("hint").classList.add("gone");
  if (!opts.token) return openSettings();
  if (mode === "listening") { stopListening(); setMode("idle"); return; }
  if (mode === "thinking" || mode === "speaking") interrupt();
  listen();
}

$("stage").addEventListener("click", onTap);

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, dialog")) {
    if (e.key === "Escape" && e.target.id === "type-input") toggleTyping(false);
    return;
  }
  if (e.code === "Space") { e.preventDefault(); onTap(); }
  else if (e.key === "Escape") { interrupt(); setMode("idle"); }
  else if (e.key === "/") { e.preventDefault(); toggleTyping(true); }
  else if (e.key === ",") openSettings();
});

function toggleTyping(show) {
  const form = $("type-form");
  form.hidden = show === undefined ? !form.hidden : !show;
  if (!form.hidden) $("type-input").focus();
}

$("keyboard-btn").addEventListener("click", () => toggleTyping());
$("type-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("type-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  audioContext();
  $("hint").classList.add("gone");
  spokenInput = false;
  send(text);
});

// --- settings -------------------------------------------------------------------------------
const dialog = $("settings");

function openSettings() {
  $("token").value = opts.token;
  $("opt-continuous").checked = opts.continuous;
  $("opt-voice").checked = opts.voice;
  $("opt-captions").checked = opts.captions;
  $("opt-stt").value = opts.stt;
  $("opt-lang").value = opts.lang;
  if (!dialog.open) dialog.showModal();
  checkServer();
}

$("settings-btn").addEventListener("click", openSettings);
$("token").addEventListener("change", () => {
  opts.token = $("token").value.trim();
  prefs.set("token", opts.token);
  checkServer();
});
for (const [id, key] of [["opt-continuous", "continuous"], ["opt-voice", "voice"], ["opt-captions", "captions"]]) {
  $(id).addEventListener("change", () => {
    opts[key] = $(id).checked;
    prefs.set(key, opts[key]);
    document.body.classList.toggle("no-captions", !opts.captions);
  });
}
for (const [id, key] of [["opt-stt", "stt"], ["opt-lang", "lang"]]) {
  $(id).addEventListener("change", () => { opts[key] = $(id).value; prefs.set(key, opts[key]); });
}
$("new-conversation").addEventListener("click", () => {
  interrupt();
  conversationId = null;
  prefs.set("conversation", null);
  caption("you", "");
  caption("alfred", "");
  setMode("idle");
  dialog.close();
});
dialog.addEventListener("close", () => {
  opts.token = $("token").value.trim();
  prefs.set("token", opts.token);
});

// --- start ------------------------------------------------------------------------------------
document.body.classList.toggle("no-captions", !opts.captions);
setMode("idle");
if (!demo) {
  if (!opts.token) $("hint").textContent = "Tap to set up";
  else checkServer();
}
window.alfred = { get mode() { return mode; }, get conversationId() { return conversationId; }, history, out };
