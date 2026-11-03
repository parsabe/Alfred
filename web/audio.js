// Audio in and out for the ribbon UI.
//
// AudioOut      plays Alfred's voice (audio events from the server), in order, and exposes its
//               loudness so the ribbon can follow it.
// BrowserVoice  fallback speech using the browser's built-in voices when the server has none.
// Mic           microphone loudness for the "listening" animation (and recording).
// BrowserRecognizer / ServerRecognizer  turn speech into text.

let sharedCtx = null;
export function audioContext() {
  if (!sharedCtx) sharedCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (sharedCtx.state === "suspended") sharedCtx.resume();
  return sharedCtx;
}

function rms(analyser, buf) {
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export class AudioOut {
  constructor() {
    this.sources = new Set();
    this.pending = 0;
    this.endAt = 0;
    this.generation = 0;
    this.chain = Promise.resolve();
    this.waiters = [];
  }

  _setup() {
    const ctx = audioContext();
    if (!this.analyser) {
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.connect(ctx.destination);
      this.buf = new Float32Array(this.analyser.fftSize);
    }
    return ctx;
  }

  get playing() { return this.sources.size > 0 || this.pending > 0; }

  enqueue(b64) {
    const ctx = this._setup();
    const gen = this.generation;
    this.pending++;
    const bytes = b64ToBytes(b64);
    // decode in order, schedule back to back so sentences flow without gaps
    this.chain = this.chain.then(async () => {
      try {
        const buffer = await ctx.decodeAudioData(bytes.buffer);
        if (gen !== this.generation) return;
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(this.analyser);
        const at = Math.max(ctx.currentTime + 0.03, this.endAt);
        this.endAt = at + buffer.duration;
        src.onended = () => { this.sources.delete(src); this._check(); };
        this.sources.add(src);
        src.start(at);
      } catch (err) {
        console.warn("could not play audio chunk", err);
      } finally {
        if (gen === this.generation) { this.pending--; this._check(); }
      }
    });
  }

  _check() {
    if (!this.playing) {
      const waiters = this.waiters;
      this.waiters = [];
      waiters.forEach((resolve) => resolve());
    }
  }

  drained() {
    return this.playing ? new Promise((resolve) => this.waiters.push(resolve)) : Promise.resolve();
  }

  level() {
    if (!this.analyser || !this.sources.size) return 0;
    return Math.min(1, rms(this.analyser, this.buf) * 3.2);
  }

  stop() {
    this.generation++;
    for (const src of this.sources) { try { src.stop(); } catch {} }
    this.sources.clear();
    this.pending = 0;
    this.endAt = 0;
    this.chain = Promise.resolve();
    this._check();
  }
}

export class BrowserVoice {
  constructor() { this.speaking = false; this._pulse = 0; }

  static available() { return "speechSynthesis" in window; }

  _pickVoice(lang) {
    const voices = speechSynthesis.getVoices();
    const want = (lang || navigator.language || "en-GB").toLowerCase();
    const base = want.split("-")[0];
    const preferred = ["daniel", "uk english male", "arthur", "oliver", "george"];
    const sameLang = voices.filter((v) => v.lang.toLowerCase().startsWith(base));
    return (
      (base === "en" && voices.find((v) => preferred.some((p) => v.name.toLowerCase().includes(p)))) ||
      sameLang.find((v) => v.lang.toLowerCase() === want) ||
      sameLang[0] ||
      null
    );
  }

  speak(text, lang) {
    if (!BrowserVoice.available() || !text.trim()) return Promise.resolve();
    return new Promise((resolve) => {
      const u = new SpeechSynthesisUtterance(text);
      const voice = this._pickVoice(lang);
      if (voice) { u.voice = voice; u.lang = voice.lang; }
      u.rate = 0.98;
      u.pitch = 0.9;
      u.onboundary = () => { this._pulse = 1; };
      u.onend = u.onerror = () => { this.speaking = false; resolve(); };
      this.speaking = true;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    });
  }

  // speechSynthesis exposes no audio, so approximate a speech-like envelope
  level() {
    if (!this.speaking) return 0;
    this._pulse *= 0.9;
    const t = performance.now() / 1000;
    return 0.3 + 0.25 * Math.abs(Math.sin(t * 7.3)) * Math.abs(Math.sin(t * 2.1)) + 0.3 * this._pulse;
  }

  stop() {
    if (BrowserVoice.available()) speechSynthesis.cancel();
    this.speaking = false;
  }
}

export class Mic {
  async open() {
    if (this.stream) return this.stream;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = audioContext();
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.buf = new Float32Array(this.analyser.fftSize);
    ctx.createMediaStreamSource(this.stream).connect(this.analyser);
    return this.stream;
  }

  raw() { return this.analyser ? rms(this.analyser, this.buf) : 0; }

  level() { return Math.min(1, Math.max(0, (this.raw() - 0.008) * 5)); }

  close() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.analyser = null;
  }
}

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const hasBrowserSTT = Boolean(SR);

// Browser speech recognition (Chrome/Edge/Safari). Note: Chrome sends audio to Google to transcribe.
export class BrowserRecognizer {
  constructor({ lang, onInterim, onFinal, onEnd, onError }) {
    this.cb = { onInterim, onFinal, onEnd, onError };
    this.rec = new SR();
    this.rec.lang = lang || navigator.language || "en-GB";
    this.rec.interimResults = true;
    this.rec.continuous = false;
    this.rec.maxAlternatives = 1;
    this.final = "";
    this.rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.final += r[0].transcript;
        else interim += r[0].transcript;
      }
      this.cb.onInterim?.((this.final + " " + interim).trim());
    };
    this.rec.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      this.cb.onError?.(e.error === "not-allowed" ? "Microphone access was refused." : `Speech recognition error: ${e.error}`);
    };
    this.rec.onend = () => {
      const text = this.final.trim();
      if (this.cancelled) return;
      text ? this.cb.onFinal?.(text) : this.cb.onEnd?.();
    };
  }
  start() { this.rec.start(); }
  stop() { this.cancelled = true; try { this.rec.abort(); } catch {} }
}

// Records with MediaRecorder, detects the end of speech by loudness, transcribes on the server.
export class ServerRecognizer {
  constructor({ mic, transcribe, lang, onInterim, onFinal, onEnd, onError }) {
    Object.assign(this, { mic, transcribe, lang });
    this.cb = { onInterim, onFinal, onEnd, onError };
  }

  start() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
    const mimeType = types.find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || "";
    this.recorder = new MediaRecorder(this.mic.stream, mimeType ? { mimeType } : undefined);
    this.chunks = [];
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.recorder.onstop = () => this._finish();
    this.recorder.start(250);

    const started = performance.now();
    let floor = 0.01, samples = 0, spoke = false, lastLoud = started;
    this.cb.onInterim?.("…");
    this.timer = setInterval(() => {
      const now = performance.now();
      const level = this.mic.raw();
      if (now - started < 350) { floor = (floor * samples + level) / ++samples; return; }
      const threshold = Math.max(0.02, floor * 3);
      if (level > threshold) { spoke = true; lastLoud = now; }
      const silentFor = now - lastLoud;
      if ((spoke && silentFor > 1100) || (!spoke && now - started > 7000) || now - started > 20000) {
        this.spoke = spoke;
        this._stopRecording();
      }
    }, 50);
  }

  _stopRecording() {
    clearInterval(this.timer);
    if (this.recorder?.state === "recording") this.recorder.stop();
  }

  async _finish() {
    if (this.cancelled) return;
    if (!this.spoke || !this.chunks.length) return this.cb.onEnd?.();
    const blob = new Blob(this.chunks, { type: this.recorder.mimeType || "audio/webm" });
    try {
      const text = (await this.transcribe(blob, this.lang)).trim();
      text ? this.cb.onFinal?.(text) : this.cb.onEnd?.();
    } catch (err) {
      this.cb.onError?.(err.message || "Transcription failed.");
    }
  }

  stop() { this.cancelled = true; this._stopRecording(); }
}
