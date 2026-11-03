// The ribbon: a luminous waveform on black. Warm lobes (white → amber → red) arch above the
// line, cool lobes (teal → azure → blue) dip below, pinched to a hairline at both ends.
//
// Rendering: each lobe is filled additively ("lighter") into an offscreen scene canvas, then
// composited onto the screen three times: two down-sampled copies for bloom, one sharp copy.

const TAU = Math.PI * 2;

// side: +1 above the line, -1 below. peak/width: where along the ribbon (0..1) the lobe swells.
// alpha: [[u, a], ...] opacity along the ribbon, so e.g. red burns hottest on the right.
const BANDS = [
  { rgb: [255, 34, 18], side: 1, amp: 1.0, peak: 0.6, width: 0.2, phase: 0.0,
    alpha: [[0, 0], [0.3, 0.05], [0.55, 0.85], [0.78, 0.7], [1, 0]] },
  { rgb: [255, 128, 22], side: 1, amp: 0.78, peak: 0.56, width: 0.19, phase: 1.3,
    alpha: [[0, 0], [0.25, 0.1], [0.5, 0.75], [0.8, 0.45], [1, 0]] },
  { rgb: [255, 214, 110], side: 1, amp: 0.55, peak: 0.47, width: 0.18, phase: 2.1,
    alpha: [[0, 0], [0.18, 0.35], [0.45, 0.8], [0.7, 0.35], [1, 0]] },
  { rgb: [255, 248, 228], side: 1, amp: 0.3, peak: 0.42, width: 0.17, phase: 2.9,
    alpha: [[0, 0], [0.12, 0.45], [0.4, 0.85], [0.65, 0.2], [1, 0]] },
  { rgb: [16, 92, 255], side: -1, amp: 0.82, peak: 0.55, width: 0.2, phase: 0.7,
    alpha: [[0, 0], [0.25, 0.4], [0.5, 0.95], [0.78, 0.6], [1, 0]] },
  { rgb: [40, 170, 255], side: -1, amp: 0.5, peak: 0.5, width: 0.19, phase: 1.9,
    alpha: [[0, 0], [0.2, 0.3], [0.45, 0.7], [0.75, 0.35], [1, 0]] },
  { rgb: [40, 235, 190], side: -1, amp: 0.24, peak: 0.47, width: 0.2, phase: 3.3,
    alpha: [[0, 0], [0.2, 0.2], [0.5, 0.65], [0.8, 0.3], [1, 0]] },
  { rgb: [215, 240, 255], side: -1, amp: 0.12, peak: 0.36, width: 0.16, phase: 4.1,
    alpha: [[0, 0], [0.15, 0.4], [0.35, 0.6], [0.6, 0.1], [1, 0]] },
];

// Strands cross the line: above it on the left, below it on the right (the teal S in the image).
const STRANDS = [
  { rgb: [60, 240, 200], amp: 0.42, left: 0.36, right: 0.6, width: 0.15, phase: 0.4, lw: 2.2,
    alpha: [[0, 0], [0.2, 0.25], [0.45, 0.9], [0.75, 0.55], [1, 0]] },
  { rgb: [170, 225, 255], amp: 0.3, left: 0.3, right: 0.52, width: 0.14, phase: 2.6, lw: 1.4,
    alpha: [[0, 0], [0.15, 0.35], [0.4, 0.7], [0.65, 0.25], [1, 0]] },
];

// How each state shapes the ribbon. base: resting height; gain: how much the live level adds;
// warm/cool: per-side emphasis; drift: how far lobes wander; speed: animation tempo.
const STATES = {
  idle:      { base: 0.1,  gain: 0.0, warm: 1.0,  cool: 1.0,  drift: 0.025, speed: 0.6 },
  listening: { base: 0.16, gain: 0.85, warm: 0.65, cool: 1.15, drift: 0.04, speed: 1.2 },
  thinking:  { base: 0.34, gain: 0.0, warm: 1.15, cool: 0.8,  drift: 0.13, speed: 1.6 },
  speaking:  { base: 0.22, gain: 0.95, warm: 1.0,  cool: 1.0,  drift: 0.05, speed: 1.4 },
  awaiting_confirmation: { base: 0.3, gain: 0.0, warm: 1.25, cool: 0.55, drift: 0.03, speed: 0.9 },
};

function lerp(a, b, k) { return a + (b - a) * k; }

function alphaAt(stops, u) {
  for (let i = 1; i < stops.length; i++) {
    if (u <= stops[i][0]) {
      const [u0, a0] = stops[i - 1];
      const [u1, a1] = stops[i];
      return lerp(a0, a1, (u - u0) / (u1 - u0 || 1));
    }
  }
  return 0;
}

export class Ribbon {
  constructor(canvas, { reducedMotion = false } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.scene = document.createElement("canvas");
    this.sctx = this.scene.getContext("2d");
    this.glowA = document.createElement("canvas");
    this.glowB = document.createElement("canvas");
    this.state = "idle";
    this.level = 0;          // live input/output loudness, 0..1 (set from outside)
    this.smoothLevel = 0;
    this.mix = { ...STATES.idle };
    this.reducedMotion = reducedMotion;
    this.levelSource = null; // optional () => number, polled every frame
    this._t = 0;
    this._last = performance.now();
    this._resize = this.resize.bind(this);
    window.addEventListener("resize", this._resize);
    this.resize();
  }

  setState(state) { if (STATES[state]) this.state = state; }
  setLevel(value) { this.level = Math.max(0, Math.min(1, value)); }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    this.dpr = dpr;
    this.w = w;
    this.h = h;
    for (const c of [this.canvas, this.scene]) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    this.glowA.width = Math.max(1, Math.round(w / 4));
    this.glowA.height = Math.max(1, Math.round(h / 4));
    this.glowB.width = Math.max(1, Math.round(w / 14));
    this.glowB.height = Math.max(1, Math.round(h / 14));
  }

  start() {
    const loop = (now) => {
      this.frame(now);
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() { cancelAnimationFrame(this._raf); }

  frame(now) {
    const dt = Math.min(0.05, (now - this._last) / 1000);
    this._last = now;
    const target = STATES[this.state];
    const k = 1 - Math.exp(-dt * 4);
    for (const key of Object.keys(target)) this.mix[key] = lerp(this.mix[key], target[key], k);
    if (this.levelSource) this.setLevel(this.levelSource());
    // fast attack, slower release: speech peaks register, silence settles gently
    const attack = this.level > this.smoothLevel ? 18 : 5;
    this.smoothLevel = lerp(this.smoothLevel, this.level, 1 - Math.exp(-dt * attack));
    this._t += dt * this.mix.speed * (this.reducedMotion ? 0.35 : 1);
    this.draw();
  }

  geometry() {
    const { w, h } = this;
    const length = Math.min(Math.max(w * 0.44, 260), 780, w * 0.92);
    return { cx: w / 2, cy: h * 0.52, length, height: length * 0.13 };
  }

  draw() {
    const { ctx, sctx, dpr, w, h } = this;
    const { cx, cy, length, height } = this.geometry();
    const m = this.mix;
    const t = this._t;
    const intensity = Math.min(1.25, m.base + m.gain * this.smoothLevel);

    sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    sctx.globalCompositeOperation = "source-over";
    sctx.clearRect(0, 0, w, h);
    sctx.globalCompositeOperation = "lighter";

    const x0 = cx - length / 2;
    const steps = 160;
    const pinch = (u) => Math.pow(Math.sin(Math.PI * u), 1.7);

    for (const band of BANDS) {
      const sideGain = band.side > 0 ? m.warm : m.cool;
      const wobble = 1 + 0.16 * Math.sin(t * 2.1 + band.phase) + 0.08 * Math.sin(t * 5.3 + band.phase * 1.7);
      const amp = band.amp * height * intensity * sideGain * wobble;
      const peak = band.peak + m.drift * Math.sin(t * 0.9 + band.phase);
      const top = [];
      for (let i = 0; i <= steps; i++) {
        const u = i / steps;
        const swell = Math.exp(-(((u - peak) / band.width) ** 2));
        const y = cy - band.side * amp * pinch(u) * swell;
        top.push([x0 + u * length, y]);
      }
      const grad = sctx.createLinearGradient(x0, 0, x0 + length, 0);
      const [r, g, b] = band.rgb;
      for (let i = 0; i <= 10; i++) {
        const u = i / 10;
        grad.addColorStop(u, `rgba(${r},${g},${b},${alphaAt(band.alpha, u).toFixed(3)})`);
      }
      // lobe body: between the centre line and the band's curve
      sctx.beginPath();
      sctx.moveTo(x0, cy);
      for (const [x, y] of top) sctx.lineTo(x, y);
      sctx.lineTo(x0 + length, cy);
      sctx.closePath();
      sctx.fillStyle = grad;
      sctx.globalAlpha = 0.34;
      sctx.fill();
      // crisp luminous edge
      sctx.beginPath();
      top.forEach(([x, y], i) => (i ? sctx.lineTo(x, y) : sctx.moveTo(x, y)));
      sctx.strokeStyle = grad;
      sctx.lineWidth = 1.6;
      sctx.globalAlpha = 0.9;
      sctx.stroke();
    }

    for (const strand of STRANDS) {
      const wobble = 1 + 0.2 * Math.sin(t * 1.7 + strand.phase);
      const amp = strand.amp * height * intensity * wobble;
      const shift = m.drift * 0.8 * Math.sin(t * 0.8 + strand.phase);
      const gauss = (u, c) => Math.exp(-(((u - c - shift) / strand.width) ** 2));
      const grad = sctx.createLinearGradient(x0, 0, x0 + length, 0);
      const [r, g, b] = strand.rgb;
      for (let i = 0; i <= 10; i++) {
        const u = i / 10;
        grad.addColorStop(u, `rgba(${r},${g},${b},${alphaAt(strand.alpha, u).toFixed(3)})`);
      }
      const pts = [];
      for (let i = 0; i <= steps; i++) {
        const u = i / steps;
        const y = cy - amp * pinch(u) * (gauss(u, strand.left) - gauss(u, strand.right));
        pts.push([x0 + u * length, y]);
      }
      sctx.beginPath();
      sctx.moveTo(x0, cy);
      for (const [x, y] of pts) sctx.lineTo(x, y);
      sctx.lineTo(x0 + length, cy);
      sctx.closePath();
      sctx.fillStyle = grad;
      sctx.globalAlpha = 0.16;
      sctx.fill();
      sctx.beginPath();
      pts.forEach(([x, y], i) => (i ? sctx.lineTo(x, y) : sctx.moveTo(x, y)));
      sctx.strokeStyle = grad;
      sctx.lineWidth = strand.lw;
      sctx.globalAlpha = 0.85;
      sctx.stroke();
    }

    // the hairline, reaching a little beyond the lobes on both sides
    const reach = length * 0.12;
    const line = sctx.createLinearGradient(x0 - reach, 0, x0 + length + reach, 0);
    line.addColorStop(0, "rgba(255,240,220,0)");
    line.addColorStop(0.3, "rgba(255,244,230,0.55)");
    line.addColorStop(0.5, "rgba(255,255,255,0.75)");
    line.addColorStop(0.7, "rgba(220,240,255,0.5)");
    line.addColorStop(1, "rgba(200,230,255,0)");
    sctx.globalAlpha = 1;
    sctx.beginPath();
    sctx.moveTo(x0 - reach, cy);
    sctx.lineTo(x0 + length + reach, cy);
    sctx.strokeStyle = line;
    sctx.lineWidth = 1.2;
    sctx.stroke();

    // composite: black, faint halo, two bloom passes, then the sharp scene
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    const halo = ctx.createRadialGradient(cx * dpr, cy * dpr, 0, cx * dpr, cy * dpr, length * 0.75 * dpr);
    const glow = Math.min(1, 0.25 + intensity * 0.6);
    halo.addColorStop(0, `rgba(60,40,22,${(0.22 * glow).toFixed(3)})`);
    halo.addColorStop(0.6, `rgba(14,18,30,${(0.12 * glow).toFixed(3)})`);
    halo.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    ctx.globalCompositeOperation = "lighter";
    ctx.imageSmoothingEnabled = true;
    const ga = this.glowA.getContext("2d");
    ga.clearRect(0, 0, this.glowA.width, this.glowA.height);
    ga.drawImage(this.scene, 0, 0, this.glowA.width, this.glowA.height);
    const gb = this.glowB.getContext("2d");
    gb.clearRect(0, 0, this.glowB.width, this.glowB.height);
    gb.drawImage(this.glowA, 0, 0, this.glowB.width, this.glowB.height);

    ctx.globalAlpha = 1.0 * glow;
    ctx.drawImage(this.glowB, 0, 0, this.canvas.width, this.canvas.height);
    ctx.globalAlpha = 0.85;
    ctx.drawImage(this.glowA, 0, 0, this.canvas.width, this.canvas.height);
    ctx.globalAlpha = 1;
    ctx.drawImage(this.scene, 0, 0);
    ctx.globalCompositeOperation = "source-over";
  }
}
