import { useEffect, useRef, useState } from "react";
import { get } from "../../api";

/** The opening: four seconds of cinema every time NODO opens (once per window). A point of light ignites, a
 *  shockwave crosses the screen, her memories burst out of the core behind it, the name writes itself and the
 *  systems report in with real numbers, over a synthesized sound (made live with WebAudio: nothing to download,
 *  no rights to pay). At the end the panel fires `nodo:intro-done` and Maria says hello and starts listening. */

const DONE = "nodo:intro-done";
let played = false;           // once per page load: every time the NODO window opens (no stale browser storage)
export const introDone = () => played || /[?&]nointro\b/.test(location.search);

function playIntro(): (() => void) | null {
  let ctx: AudioContext;
  try { ctx = new AudioContext(); } catch { return null; }
  if (ctx.state !== "running") { void ctx.close(); return null; }   // a normal tab without autoplay: silence
  const t0 = ctx.currentTime + 0.05;
  const master = ctx.createGain(); master.gain.value = 0.55;
  const comp = ctx.createDynamicsCompressor();
  master.connect(comp).connect(ctx.destination);
  const echo = ctx.createDelay(1); echo.delayTime.value = 0.23;
  const fb = ctx.createGain(); fb.gain.value = 0.38;
  const wet = ctx.createGain(); wet.gain.value = 0.5;
  echo.connect(fb).connect(echo); echo.connect(wet).connect(master);

  // 1. the hum of something waking up
  const sub = ctx.createOscillator(), subG = ctx.createGain(), lp = ctx.createBiquadFilter();
  sub.type = "sine"; sub.frequency.setValueAtTime(41, t0); sub.frequency.exponentialRampToValueAtTime(82, t0 + 2.6);
  lp.type = "lowpass"; lp.frequency.value = 300;
  subG.gain.setValueAtTime(0, t0); subG.gain.linearRampToValueAtTime(0.35, t0 + 1.4);
  subG.gain.linearRampToValueAtTime(0.0001, t0 + 4.2);
  sub.connect(lp).connect(subG).connect(master); sub.start(t0); sub.stop(t0 + 4.3);

  // 2. the shockwave: filtered noise sweeping up
  const len = ctx.sampleRate * 1.6, buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
  const noise = ctx.createBufferSource(), bp = ctx.createBiquadFilter(), nG = ctx.createGain();
  noise.buffer = buf; bp.type = "bandpass"; bp.Q.value = 1.4;
  bp.frequency.setValueAtTime(180, t0 + 0.55); bp.frequency.exponentialRampToValueAtTime(5200, t0 + 1.7);
  nG.gain.setValueAtTime(0, t0 + 0.55); nG.gain.linearRampToValueAtTime(0.32, t0 + 0.9);
  nG.gain.exponentialRampToValueAtTime(0.0001, t0 + 2.1);
  noise.connect(bp).connect(nG).connect(master); noise.start(t0 + 0.55);

  // 3. sparks: a bright lydian arpeggio thrown into the echo
  const notes = [659.25, 987.77, 1479.98, 1661.22, 1975.53, 2959.96];
  notes.forEach((f, i) => {
    const o = ctx.createOscillator(), g = ctx.createGain(), at = t0 + 1.0 + i * 0.085;
    o.type = "triangle"; o.frequency.value = f;
    g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(0.09, at + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.5);
    o.connect(g); g.connect(master); g.connect(echo); o.start(at); o.stop(at + 0.6);
  });

  // 4. arrival: a wide chord opening up
  [164.81, 246.94, 415.3, 493.88].forEach((f, i) => {
    [-6, 6].forEach((det) => {
      const o = ctx.createOscillator(), g = ctx.createGain(), f2 = ctx.createBiquadFilter(), at = t0 + 2.2;
      o.type = "sawtooth"; o.frequency.value = f; o.detune.value = det + i;
      f2.type = "lowpass"; f2.frequency.setValueAtTime(300, at); f2.frequency.exponentialRampToValueAtTime(2600, at + 1.3);
      g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(0.035, at + 0.45); g.gain.exponentialRampToValueAtTime(0.0001, at + 3.4);
      o.connect(f2).connect(g); g.connect(master); g.connect(echo); o.start(at); o.stop(at + 3.5);
    });
  });
  const stop = setTimeout(() => void ctx.close(), 6500);
  return () => { clearTimeout(stop); void ctx.close(); };
}

export function Intro({ name }: { name: string }) {
  const [gone, setGone] = useState(() => introDone() || matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [phase, setPhase] = useState(0);
  const [lines, setLines] = useState<string[]>([]);
  const sound = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (gone) { window.dispatchEvent(new Event(DONE)); return; }
    sound.current = playIntro();
    const facts: string[] = ["occhi · orecchie · voce · mani", "voce dal vivo in linea"];
    get<{ stats?: Record<string, number> }>("/api/v1/mind/graph").then((g) => {
      const s = g?.stats || {};
      facts.unshift(`memoria · ${s.ricordi ?? 0} ricordi · ${(s.collegamenti ?? 0).toLocaleString("it-IT")} collegamenti`);
      if (s.conversazioni) facts.push(`${s.conversazioni.toLocaleString("it-IT")} conversazioni lette`);
      if (s.clienti) facts.push(`registro · ${s.clienti} clienti`);
    }).catch(() => undefined);
    const timers = [
      setTimeout(() => setPhase(1), 450),                 // ignition
      setTimeout(() => setPhase(2), 1200),                // name
      ...[0, 1, 2, 3, 4].map((i) => setTimeout(() => setLines(facts.slice(0, i + 1)), 1700 + i * 330)),
      setTimeout(() => setPhase(3), 3700),                // fade out
      setTimeout(() => finish(), 4400),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const finish = () => {
    played = true;
    setGone(true);
    window.dispatchEvent(new Event(DONE));
  };

  if (gone) return null;
  return <div className={`intro p${phase}`} onClick={finish} role="presentation">
    <div className="intro-core" />
    <div className="intro-wave" />
    <div className="intro-wave w2" />
    <div className="intro-scan" />
    <div className="intro-title">
      <h1>{name.split("").map((ch, i) => <span key={i} style={{ animationDelay: `${i * 90}ms` }}>{ch}</span>)}</h1>
      <p>MARIA · SISTEMA ONLINE</p>
    </div>
    <ol className="intro-lines">{lines.map((l, i) => <li key={i}><i>{String(i + 1).padStart(2, "0")}</i>{l}<b>OK</b></li>)}</ol>
  </div>;
}

export const INTRO_DONE_EVENT = DONE;
