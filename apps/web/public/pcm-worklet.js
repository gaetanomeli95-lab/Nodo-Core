/* NODO voce dal vivo: prende i campioni del microfono e li passa al thread principale,
   che li converte in PCM 16 kHz per Gemini Live. */
class PcmWorklet extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
