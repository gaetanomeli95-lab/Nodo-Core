/** A backing band, made live with WebAudio (nothing to download, no rights): drums, bass and chords under Maria's
 *  voice when she sings. Quiet on purpose — the voice leads. Stops by itself after two minutes. */

type Style = { prog: number[][]; bpm: number; swing?: number; kick: number[]; snare: number[]; hat: number; wave: OscillatorType };

const C = (n: number) => 261.63 * Math.pow(2, n / 12);               // semitones from middle C
const STYLES: Record<string, Style> = {
  pop: { prog: [[0, 4, 7], [7, 11, 14], [9, 12, 16], [5, 9, 12]], bpm: 104, kick: [0, 2.5], snare: [1, 3], hat: 2, wave: "triangle" },
  ballata: { prog: [[0, 4, 7], [9, 12, 16], [5, 9, 12], [7, 11, 14]], bpm: 72, kick: [0], snare: [2], hat: 1, wave: "sine" },
  rap: { prog: [[9, 12, 16], [9, 12, 16], [5, 9, 12], [7, 11, 14]], bpm: 92, swing: 0.12, kick: [0, 0.75, 2.5], snare: [1, 3], hat: 4, wave: "sawtooth" },
  rock: { prog: [[0, 7, 12], [5, 12, 17], [7, 14, 19], [5, 12, 17]], bpm: 128, kick: [0, 1.5, 2, 3.5], snare: [1, 3], hat: 2, wave: "sawtooth" },
  tarantella: { prog: [[9, 12, 16], [4, 8, 11], [4, 8, 11], [9, 12, 16]], bpm: 150, kick: [0, 1.5, 3], snare: [], hat: 3, wave: "square" },
  "ninna nanna": { prog: [[0, 4, 7], [5, 9, 12], [0, 4, 7], [7, 11, 14]], bpm: 60, kick: [], snare: [], hat: 0, wave: "sine" },
  lirica: { prog: [[0, 4, 7], [5, 9, 12], [7, 11, 14], [0, 4, 7]], bpm: 66, kick: [], snare: [], hat: 0, wave: "triangle" },
};

let current: { stop: (fade?: number) => void } | null = null;

export function stopMusic(fade = 1.2) { current?.stop(fade); current = null; }

export function startMusic(styleName = "pop", bpmWanted?: number) {
  stopMusic(0.2);
  let ctx: AudioContext;
  try { ctx = new AudioContext(); } catch { return; }
  const st = STYLES[(styleName || "pop").toLowerCase()] || STYLES.pop;
  const bpm = Math.max(50, Math.min(170, bpmWanted || st.bpm)), beat = 60 / bpm, bar = beat * 4;
  const master = ctx.createGain(); master.gain.value = 0;
  master.gain.linearRampToValueAtTime(0.16, ctx.currentTime + 1.5);   // under the voice, never over it
  master.connect(ctx.destination);
  const noise = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);
  const nd = noise.getChannelData(0); for (let i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;

  const kick = (t: number) => { const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.setValueAtTime(140, t); o.frequency.exponentialRampToValueAtTime(40, t + 0.18);
    g.gain.setValueAtTime(0.9, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.25);
    o.connect(g).connect(master); o.start(t); o.stop(t + 0.3); };
  const hit = (t: number, f: number, dur: number, vol: number) => { const s = ctx.createBufferSource(), bp = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = noise; bp.type = f > 5000 ? "highpass" : "bandpass"; bp.frequency.value = f;
    g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    s.connect(bp).connect(g).connect(master); s.start(t); s.stop(t + dur + 0.05); };
  const note = (t: number, f: number, dur: number, vol: number, type: OscillatorType) => { const o = ctx.createOscillator(), g = ctx.createGain(), lp = ctx.createBiquadFilter();
    o.type = type; o.frequency.value = f; lp.type = "lowpass"; lp.frequency.value = 1800;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.03); g.gain.setValueAtTime(vol, t + dur * 0.7);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    o.connect(lp).connect(g).connect(master); o.start(t); o.stop(t + dur + 0.05); };

  let next = ctx.currentTime + 0.1, barN = 0, timer = 0;
  const schedule = () => {
    while (next < ctx.currentTime + 1.2) {                  // a bar at a time, a little ahead of the clock
      const chord = st.prog[barN % st.prog.length];
      for (const b of st.kick) kick(next + b * beat);
      for (const b of st.snare) hit(next + b * beat, 1800, 0.18, 0.5);
      for (let h = 0; h < 4 * st.hat; h++) hit(next + (h / st.hat) * beat + (h % 2 ? (st.swing || 0) * beat : 0), 8000, 0.05, 0.18);
      note(next, C(chord[0] - 24), bar * 0.95, 0.35, "triangle");                       // bass
      for (const n of chord) note(next, C(n), bar * 0.98, 0.07, st.wave);              // chords
      if (!st.kick.length) for (let a = 0; a < 4; a++) note(next + a * beat, C(chord[a % 3] + 12), beat * 0.9, 0.05, "sine");  // arpeggio
      next += bar; barN++;
    }
  };
  schedule(); timer = window.setInterval(schedule, 300);
  const auto = window.setTimeout(() => stopMusic(3), 120_000);
  window.dispatchEvent(new CustomEvent("nodo:music", { detail: { on: true, style: styleName } }));
  current = { stop: (fade = 1.2) => {
    clearInterval(timer); clearTimeout(auto);
    const now = ctx.currentTime;
    master.gain.cancelScheduledValues(now); master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(0, now + fade);
    setTimeout(() => void ctx.close(), fade * 1000 + 200);
    window.dispatchEvent(new CustomEvent("nodo:music", { detail: { on: false } }));
  } };
}
