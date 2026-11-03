// Runs on the audio thread: converts the microphone to 16 kHz mono 16-bit PCM and posts
// 20 ms frames (320 samples) to the page, which streams them to Alfred's server.
class AlfredCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // e.g. 3 at 48 kHz
    this.frame = new Int16Array(320);
    this.n = 0;
    this.acc = 0;
    this.count = 0;
    this.pos = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      // box-filter decimation: average the input samples that fall into each output sample
      this.acc += channel[i];
      this.count++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const s = Math.max(-1, Math.min(1, this.acc / this.count));
        this.frame[this.n++] = s * 32767;
        this.acc = 0;
        this.count = 0;
        if (this.n === this.frame.length) {
          this.port.postMessage(this.frame.buffer.slice(0), []);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("alfred-capture", AlfredCapture);
