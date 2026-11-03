// Audio in and out for the ribbon UI.
//
// Microphone   streams 16 kHz PCM frames (via an AudioWorklet) and exposes loudness for the ribbon.
// AudioOut     plays Alfred's voice in order, can be ducked or stopped, exposes loudness.
// BrowserVoice stand-in speech using the browser's own voices if the server has none.

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

export class Microphone {
  constructor(onFrame) {
    this.onFrame = onFrame; // (ArrayBuffer of Int16 PCM, 20 ms) => void
    this.muted = false;
  }

  async start() {
    if (this.stream) return;
    const ctx = audioContext();
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    await ctx.audioWorklet.addModule("capture-worklet.js");
    const source = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, "alfred-capture");
    this.node.port.onmessage = (e) => { if (!this.muted) this.onFrame(e.data); };
    source.connect(this.node);
    // keep the worklet running without making any sound
    const sink = ctx.createGain();
    sink.gain.value = 0;
    this.node.connect(sink).connect(ctx.destination);
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.buf = new Float32Array(this.analyser.fftSize);
    source.connect(this.analyser);
  }

  level() {
    if (!this.analyser || this.muted) return 0;
    return Math.min(1, Math.max(0, (rms(this.analyser, this.buf) - 0.008) * 5));
  }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.node?.disconnect();
    this.stream = this.node = this.analyser = null;
  }
}

export class AudioOut {
  constructor() {
    this.sources = new Set();
    this.pending = 0;
    this.endAt = 0;
    this.generation = 0;
    this.chain = Promise.resolve();
    this.waiters = [];
    this.muted = false;
  }

  _setup() {
    const ctx = audioContext();
    if (!this.gain) {
      this.gain = ctx.createGain();
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.buf = new Float32Array(this.analyser.fftSize);
      this.gain.connect(this.analyser);
      this.analyser.connect(ctx.destination);
    }
    return ctx;
  }

  get playing() { return this.sources.size > 0 || this.pending > 0; }

  enqueue(b64) {
    if (this.muted) return;
    const ctx = this._setup();
    const gen = this.generation;
    this.pending++;
    const bytes = b64ToBytes(b64);
    this.chain = this.chain.then(async () => {
      try {
        const buffer = await ctx.decodeAudioData(bytes.buffer);
        if (gen !== this.generation) return;
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(this.gain);
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

  duck(on) {
    if (!this.gain) return;
    const ctx = audioContext();
    this.gain.gain.cancelScheduledValues(ctx.currentTime);
    this.gain.gain.setTargetAtTime(on ? 0.2 : 1, ctx.currentTime, 0.05);
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
    this.duck(false);
    this._check();
  }
}

export class BrowserVoice {
  constructor() { this.speaking = false; this._pulse = 0; }

  static available() { return "speechSynthesis" in window; }

  _pickVoice() {
    const voices = speechSynthesis.getVoices();
    const preferred = ["daniel", "uk english male", "arthur", "oliver", "george"];
    return (
      voices.find((v) => preferred.some((p) => v.name.toLowerCase().includes(p))) ||
      voices.find((v) => v.lang.toLowerCase().startsWith("en-gb")) ||
      null
    );
  }

  speak(text) {
    if (!BrowserVoice.available() || !text.trim()) return Promise.resolve();
    return new Promise((resolve) => {
      const u = new SpeechSynthesisUtterance(text);
      const voice = this._pickVoice();
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
