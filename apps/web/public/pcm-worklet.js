/* Continuous resampling with fractional phase across render quanta. One message per
   20 ms, instead of one JSON frame per 128 source samples. Output is PCM16 LE, mono. */
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.sum = 0;
    this.weight = 0;
    this.packet = new Int16Array(320);
    this.used = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (const sample of ch) {
      // Area averaging is a small box low-pass filter; carry the fractional
      // source sample into the next output sample (44.1/48 kHz both keep time).
      let remaining = 1;
      const ratio = sampleRate / 16000;
      while (remaining > 1e-8) {
        const take = Math.min(remaining, ratio - this.weight);
        this.sum += sample * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= ratio - 1e-8) {
          const value = Math.max(-1, Math.min(1, this.sum / ratio));
          this.packet[this.used++] = Math.round(value * (value < 0 ? 32768 : 32767));
          this.sum = 0;
          this.weight = 0;
          if (this.used === 320) {
            this.port.postMessage(this.packet.buffer, [this.packet.buffer]);
            this.packet = new Int16Array(320);
            this.used = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
