import { useEffect, useRef, useState } from "react";
import { get } from "../../api";
import { calm, canvasDpr } from "../../motion";

/** The opening, every time NODO opens (eight seconds, a click or any key skips it).
 *  Built like the «Jarvis» boots people film: a black room and a grid; light streams in from every side and is
 *  pulled into a point; the reactor assembles ring after ring; ignition, a shockwave that lights a hexagonal grid
 *  across the screen; the systems report in with REAL numbers (memories, links, old chats, customers) while gauges
 *  fill; the name decodes itself; then everything folds back into the core and Maria says hello.
 *  One canvas, one animation loop, no glow filters (too heavy for a small PC): additive light and gradients only.
 *  The sound is synthesized live with WebAudio: nothing to download, no rights to pay. */

const DONE = "nodo:intro-done";
const TOTAL = 8.2;                 // seconds
let played = false;                // once per page load: every time the NODO window opens
export const introDone = () => played || calm || /[?&]nointro\b/.test(location.search);

type Stats = Record<string, number>;

/* ------------------------------------------------------------------ sound */
function playIntro(): (() => void) | null {
  let ctx: AudioContext;
  try { ctx = new AudioContext(); } catch { return null; }
  if (ctx.state !== "running") { void ctx.close(); return null; }   // a normal tab without autoplay: silence
  const t0 = ctx.currentTime + 0.05;
  const master = ctx.createGain(); master.gain.value = 0.6;
  const comp = ctx.createDynamicsCompressor();
  master.connect(comp).connect(ctx.destination);
  const echo = ctx.createDelay(1); echo.delayTime.value = 0.27;
  const fb = ctx.createGain(); fb.gain.value = 0.36;
  const wet = ctx.createGain(); wet.gain.value = 0.45;
  echo.connect(fb).connect(echo); echo.connect(wet).connect(master);
  const noiseBuf = (secs: number) => {
    const len = Math.floor(ctx.sampleRate * secs), b = ctx.createBuffer(1, len, ctx.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    return b;
  };
  const tone = (type: OscillatorType, f: number, at: number, dur: number, vol: number, send = false, f2?: number) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.setValueAtTime(f, at);
    if (f2) o.frequency.exponentialRampToValueAtTime(f2, at + dur);
    g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(vol, at + Math.min(0.02, dur / 4));
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    o.connect(g); g.connect(master); if (send) g.connect(echo);
    o.start(at); o.stop(at + dur + 0.05);
  };

  // 1. the room wakes: a deep hum that rises while the light is pulled in
  const sub = ctx.createOscillator(), subG = ctx.createGain(), lp = ctx.createBiquadFilter();
  sub.type = "sawtooth"; sub.frequency.setValueAtTime(36, t0); sub.frequency.exponentialRampToValueAtTime(72, t0 + 2.6);
  lp.type = "lowpass"; lp.frequency.setValueAtTime(90, t0); lp.frequency.exponentialRampToValueAtTime(420, t0 + 2.6);
  subG.gain.setValueAtTime(0, t0); subG.gain.linearRampToValueAtTime(0.22, t0 + 2.2);
  subG.gain.linearRampToValueAtTime(0.0001, t0 + 2.75);
  sub.connect(lp).connect(subG).connect(master); sub.start(t0); sub.stop(t0 + 2.8);

  // 2. the inrush: noise sweeping up into the core
  const rush = ctx.createBufferSource(), bp = ctx.createBiquadFilter(), rG = ctx.createGain();
  rush.buffer = noiseBuf(2.4); bp.type = "bandpass"; bp.Q.value = 2.2;
  bp.frequency.setValueAtTime(220, t0 + 0.3); bp.frequency.exponentialRampToValueAtTime(6200, t0 + 2.6);
  rG.gain.setValueAtTime(0, t0 + 0.3); rG.gain.linearRampToValueAtTime(0.16, t0 + 2.4); rG.gain.linearRampToValueAtTime(0, t0 + 2.62);
  rush.connect(bp).connect(rG).connect(master); rush.start(t0 + 0.3);

  // 3. rings locking in place
  [0.55, 0.9, 1.25, 1.6, 1.95].forEach((s, i) => tone("square", 1400 + i * 260, t0 + s, 0.05, 0.035, true));

  // 4. ignition: a drop of the sub, a burst of air, a bright crack
  tone("sine", 140, t0 + 2.6, 1.4, 0.55, false, 32);
  const burst = ctx.createBufferSource(), bf = ctx.createBiquadFilter(), bG = ctx.createGain();
  burst.buffer = noiseBuf(1.6); bf.type = "lowpass"; bf.frequency.setValueAtTime(5000, t0 + 2.6);
  bf.frequency.exponentialRampToValueAtTime(200, t0 + 4.0);
  bG.gain.setValueAtTime(0.4, t0 + 2.6); bG.gain.exponentialRampToValueAtTime(0.0001, t0 + 4.1);
  burst.connect(bf).connect(bG).connect(master); burst.start(t0 + 2.6);
  tone("triangle", 2637, t0 + 2.62, 0.6, 0.06, true);

  // 5. systems reporting: data blips while the log types
  for (let i = 0; i < 16; i++) tone("square", 1800 + ((i * 7) % 5) * 220, t0 + 3.1 + i * 0.19, 0.035, 0.022);

  // 6. the theme: a wide, slowly opening chord under it all
  [110, 164.81, 220, 277.18, 329.63].forEach((f, i) => {
    [-7, 7].forEach((det) => {
      const o = ctx.createOscillator(), g = ctx.createGain(), f2 = ctx.createBiquadFilter(), at = t0 + 2.75;
      o.type = "sawtooth"; o.frequency.value = f; o.detune.value = det + i;
      f2.type = "lowpass"; f2.frequency.setValueAtTime(240, at); f2.frequency.exponentialRampToValueAtTime(2400, at + 3.2);
      g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(0.03, at + 1.2);
      g.gain.setValueAtTime(0.03, at + 3.7); g.gain.exponentialRampToValueAtTime(0.0001, at + 5.2);
      o.connect(f2).connect(g); g.connect(master); g.connect(echo); o.start(at); o.stop(at + 5.3);
    });
  });

  // 7. fold back into the core, and a last clear note: she is here
  const fold = ctx.createBufferSource(), ff = ctx.createBiquadFilter(), fG = ctx.createGain();
  fold.buffer = noiseBuf(1.2); ff.type = "bandpass"; ff.Q.value = 1.5;
  ff.frequency.setValueAtTime(5000, t0 + 6.5); ff.frequency.exponentialRampToValueAtTime(300, t0 + 7.4);
  fG.gain.setValueAtTime(0, t0 + 6.5); fG.gain.linearRampToValueAtTime(0.14, t0 + 7.1); fG.gain.linearRampToValueAtTime(0, t0 + 7.45);
  fold.connect(ff).connect(fG).connect(master); fold.start(t0 + 6.5);
  [659.25, 987.77, 1318.51].forEach((f, i) => tone("sine", f, t0 + 7.42 + i * 0.07, 1.6, 0.07, true));

  const stop = setTimeout(() => void ctx.close(), 11000);
  return () => { clearTimeout(stop); void ctx.close(); };
}

/* ------------------------------------------------------------------ picture */
const clamp = (x: number) => Math.max(0, Math.min(1, x));
const seg = (t: number, a: number, b: number) => clamp((t - a) / (b - a));
const outC = (x: number) => 1 - (1 - x) ** 3;
const inC = (x: number) => x * x * x;
const GLYPHS = "01ΛΔΣΞΦΨ#/<>*+=";

function hexGrid(w: number, h: number, size: number): HTMLCanvasElement {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d")!;
  g.strokeStyle = "rgba(110,235,255,0.9)"; g.lineWidth = 1;
  const dx = size * Math.sqrt(3), dy = size * 1.5;
  g.beginPath();
  for (let row = -1, y = 0; y < h + size; row++, y = row * dy) {
    for (let x = (row % 2 ? dx / 2 : 0) - dx; x < w + dx; x += dx) {
      for (let k = 0; k < 6; k++) {
        const a = Math.PI / 6 + (k * Math.PI) / 3, px = x + size * 0.92 * Math.cos(a), py = y + size * 0.92 * Math.sin(a);
        if (k) g.lineTo(px, py); else g.moveTo(px, py);
      }
      g.closePath();
    }
  }
  g.stroke();
  return c;
}

export function Intro({ name }: { name: string }) {
  const [gone, setGone] = useState(introDone);
  const [leaving, setLeaving] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const stats = useRef<Stats>({});
  const finished = useRef(false);
  const leavingRef = useRef(false);
  const stopSound = useRef<(() => void) | null>(null);

  const finish = (skipped = false) => {
    if (finished.current) return;
    finished.current = true;
    if (skipped) stopSound.current?.();            // skipped: silence at once; a natural end lets the last note ring
    played = true;
    setGone(true);
    window.dispatchEvent(new Event(DONE));
  };

  useEffect(() => {
    if (gone) { window.dispatchEvent(new Event(DONE)); return; }
    get<{ stats?: Stats }>("/api/v1/mind/graph").then((g) => { stats.current = g?.stats || {}; }).catch(() => undefined);
    const el = canvas.current!;
    const ctx = el.getContext("2d");
    if (!ctx) { finish(); return; }
    let raf = 0, start = 0, sound: (() => void) | null = null, W = 0, H = 0, grid: HTMLCanvasElement | null = null;
    const dpr = canvasDpr(1.25);
    const resize = () => {
      W = Math.round(innerWidth * dpr); H = Math.round(innerHeight * dpr);
      el.width = W; el.height = H; grid = hexGrid(W, H, 26 * dpr);
    };
    resize(); addEventListener("resize", resize);
    const parts = Array.from({ length: 260 }, () => ({
      a: Math.random() * Math.PI * 2, r: 0.55 + Math.random() * 0.75, d: 0.25 + Math.random() * 1.1,
      spin: (Math.random() < 0.5 ? -1 : 1) * (1.2 + Math.random() * 1.6), hue: Math.random() < 0.18,
    }));
    const lines = (): string[] => {
      const s = stats.current, it = (n?: number) => (n ?? 0).toLocaleString("it-IT");
      return [
        "NUCLEO NEURALE ........................ ATTIVO",
        `MEMORIA ............ ${it(s.ricordi)} RICORDI · ${it(s.collegamenti)} COLLEGAMENTI`,
        `ARCHIVIO ............ ${s.conversazioni ? it(s.conversazioni) + " CONVERSAZIONI LETTE" : "PRONTO"}`,
        `REGISTRO NEGOZIO ......... ${s.clienti ? it(s.clienti) + " CLIENTI" : "IN LINEA"}`,
        "SQUADRA ............. 6 AGENTI AL LORO POSTO",
        "VOCE DAL VIVO ................. IT-IT · PRONTA",
        "PROTOCOLLI DI SICUREZZA ................ OK",
      ];
    };
    const gauges = ["MEMORIA", "VOCE", "RETE", "ENERGIA"];

    const draw = (now: number) => {
      if (finished.current) return;
      raf = requestAnimationFrame(draw);
      if (!start) start = now;
      const T = (now - start) / 1000;
      if (T >= TOTAL) { finish(); return; }
      if (T > TOTAL - 0.75 && !leavingRef.current) { leavingRef.current = true; setLeaving(true); }
      const S = Math.min(W * 0.6, H) * 0.9, cx = W / 2, cy = H * 0.54, diag = Math.hypot(W, H);
      const fold = inC(seg(T, 6.5, 7.45));           // everything goes back into the core
      const scale = 1 - 0.88 * fold;
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = "#010306"; ctx.fillRect(0, 0, W, H);

      // the room: a faint square grid, a vignette
      const gridA = 0.06 * seg(T, 0, 0.8) * (1 - fold);
      if (gridA > 0) {
        ctx.strokeStyle = `rgba(80,200,230,${gridA})`; ctx.lineWidth = 1;
        const step = 64 * dpr; ctx.beginPath();
        for (let x = cx % step; x < W; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
        for (let y = cy % step; y < H; y += step) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
        ctx.stroke();
      }

      ctx.globalCompositeOperation = "lighter";
      // the hexagonal grid lit by the shockwave, then left glowing faintly
      if (grid && T > 2.6) {
        const Rw = outC(seg(T, 2.6, 4.1)) * diag * 0.75;
        ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, Rw, 0, Math.PI * 2);
        ctx.arc(cx, cy, Math.max(0, Rw - 140 * dpr), 0, Math.PI * 2, true); ctx.clip();
        ctx.globalAlpha = 0.5 * (1 - seg(T, 3.6, 4.4)); ctx.drawImage(grid, 0, 0); ctx.restore();
        const rest = 0.07 * seg(T, 3.0, 3.8) * (1 - fold);
        if (rest > 0) {
          ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, Math.min(Rw, S * 0.62), 0, Math.PI * 2); ctx.clip();
          ctx.globalAlpha = rest; ctx.drawImage(grid, 0, 0); ctx.restore();
        }
        if (T < 4.2) {                               // the wave front
          ctx.strokeStyle = `rgba(150,245,255,${0.7 * (1 - seg(T, 2.6, 4.2))})`; ctx.lineWidth = 3 * dpr;
          ctx.beginPath(); ctx.arc(cx, cy, Rw, 0, Math.PI * 2); ctx.stroke();
          ctx.strokeStyle = `rgba(201,178,255,${0.45 * (1 - seg(T, 2.8, 4.2))})`; ctx.lineWidth = 2 * dpr;
          ctx.beginPath(); ctx.arc(cx, cy, Rw * 0.82, 0, Math.PI * 2); ctx.stroke();
        }
      }

      // the inrush: streaks of light spiralling into the point
      if (T < 2.75) {
        ctx.lineWidth = 1.4 * dpr;
        for (const p of parts) {
          const k = seg(T, p.d * 0.9, p.d * 0.9 + 1.5);
          if (k <= 0 || k >= 1) continue;
          const e0 = inC(Math.max(0, k - 0.06)), e1 = inC(k);
          const r0 = p.r * diag * 0.6 * (1 - e0), r1 = p.r * diag * 0.6 * (1 - e1);
          const a0 = p.a + p.spin * e0, a1 = p.a + p.spin * e1;
          ctx.strokeStyle = p.hue ? `rgba(255,210,122,${0.25 + 0.6 * k})` : `rgba(110,240,255,${0.2 + 0.7 * k})`;
          ctx.beginPath(); ctx.moveTo(cx + Math.cos(a0) * r0, cy + Math.sin(a0) * r0);
          ctx.lineTo(cx + Math.cos(a1) * r1, cy + Math.sin(a1) * r1); ctx.stroke();
        }
      }

      // the reactor: rings assembling one after the other, each turning its own way
      const rings: [number, number, number, number, string][] = [   // radius, start, speed, kind, color
        [0.085, 0.45, 0.9, 0, "110,240,255"], [0.115, 0.8, -1.4, 1, "110,240,255"], [0.15, 1.15, 0.5, 2, "150,245,255"],
        [0.19, 1.5, -0.7, 3, "201,178,255"], [0.235, 1.85, 0.3, 1, "110,240,255"], [0.29, 2.2, -0.18, 4, "255,210,122"],
        [0.35, 2.9, 0.12, 2, "110,240,255"],
      ];
      rings.forEach(([rr, st, sp, kind, col], i) => {
        const build = outC(seg(T, st, st + 0.55));
        if (build <= 0) return;
        const pulse = T > 2.6 ? 1 + 0.06 * Math.exp(-(T - 2.6) * 3) * Math.sin((T - 2.6) * 30 + i) : 1;
        const R = rr * S * scale * pulse, rot = sp * T + i, alpha = (0.35 + 0.5 * build) * (1 - fold * 0.9);
        ctx.strokeStyle = `rgba(${col},${alpha})`; ctx.fillStyle = `rgba(${col},${alpha})`;
        ctx.lineWidth = (kind === 0 ? 2.5 : 1.4) * dpr;
        if (kind === 0 || kind === 3) {                      // a full ring, drawn on as it builds
          ctx.beginPath(); ctx.arc(cx, cy, R, rot, rot + Math.PI * 2 * build); ctx.stroke();
          if (kind === 3) { ctx.setLineDash([3 * dpr, 9 * dpr]); ctx.beginPath(); ctx.arc(cx, cy, R + 7 * dpr, -rot, -rot + Math.PI * 2 * build); ctx.stroke(); ctx.setLineDash([]); }
        } else if (kind === 1) {                             // three thick arcs with gaps
          ctx.lineWidth = 5 * dpr;
          for (let k = 0; k < 3; k++) {
            ctx.beginPath(); ctx.arc(cx, cy, R, rot + (k * Math.PI * 2) / 3, rot + (k * Math.PI * 2) / 3 + 1.4 * build); ctx.stroke();
          }
        } else if (kind === 2) {                             // a dial of ticks
          const n = 96, upto = Math.floor(n * build);
          ctx.beginPath();
          for (let k = 0; k < upto; k++) {
            const a = rot + (k / n) * Math.PI * 2, long = k % 8 === 0 ? 10 : 4;
            ctx.moveTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
            ctx.lineTo(cx + Math.cos(a) * (R + long * dpr), cy + Math.sin(a) * (R + long * dpr));
          }
          ctx.stroke();
        } else {                                             // amber segments with small markers
          for (let k = 0; k < 8; k++) {
            const a = rot + (k * Math.PI) / 4;
            ctx.beginPath(); ctx.arc(cx, cy, R, a, a + 0.42 * build); ctx.stroke();
            ctx.beginPath(); ctx.arc(cx + Math.cos(a) * R, cy + Math.sin(a) * R, 2.2 * dpr, 0, Math.PI * 2); ctx.fill();
          }
        }
      });

      // the radar sweep across the reactor
      if (T > 1.2 && fold < 1) {
        const R = 0.36 * S * scale, a = T * 2.2;
        const cg = ctx.createConicGradient(a, cx, cy);
        cg.addColorStop(0, `rgba(110,240,255,${0.22 * (1 - fold)})`); cg.addColorStop(0.12, "rgba(110,240,255,0)");
        cg.addColorStop(1, "rgba(110,240,255,0)");
        ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
      }

      // the core: a point that becomes a star
      const ign = seg(T, 2.45, 2.75), after = T > 2.6 ? Math.exp(-(T - 2.6) * 2.2) : 0;
      const coreR = (2 + 10 * seg(T, 0.2, 2.5) + 26 * ign - 10 * fold) * dpr * (1 + 0.08 * Math.sin(T * 9)) + 60 * dpr * after;
      const g1 = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(4, coreR * 4));
      g1.addColorStop(0, "rgba(255,255,255,1)"); g1.addColorStop(0.18, "rgba(170,250,255,0.9)");
      g1.addColorStop(0.45, "rgba(70,216,229,0.28)"); g1.addColorStop(1, "rgba(70,216,229,0)");
      ctx.fillStyle = g1; ctx.beginPath(); ctx.arc(cx, cy, Math.max(4, coreR * 4), 0, Math.PI * 2); ctx.fill();
      if (T > 7.3) {                                 // the last spark before the room opens
        const k = seg(T, 7.3, 7.9), r = (20 + 500 * outC(k)) * dpr;
        ctx.strokeStyle = `rgba(200,250,255,${0.8 * (1 - k)})`; ctx.lineWidth = 2 * dpr;
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.globalCompositeOperation = "source-over";
      if (T > 2.6 && T < 3.2) {                      // ignition flash
        ctx.fillStyle = `rgba(220,250,255,${0.85 * (1 - seg(T, 2.6, 3.15))})`; ctx.fillRect(0, 0, W, H);
      }

      // words: the name decoding itself, the log typing, the gauges filling
      const words = (1 - seg(T, 6.4, 7.0));
      if (T > 3.0 && words > 0) {
        const title = name.toUpperCase(), fs = Math.round(S * 0.085);
        ctx.font = `200 ${fs}px "Segoe UI", Inter, system-ui, sans-serif`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        const sp = fs * 0.55, total = (title.length - 1) * (fs * 0.62 + sp);
        for (let i = 0; i < title.length; i++) {
          const settle = 3.15 + i * 0.16 + 0.45, a = seg(T, 3.0 + i * 0.08, 3.4 + i * 0.08) * words;
          const ch = T < settle ? GLYPHS[Math.floor(T * 22 + i * 5) % GLYPHS.length] : title[i];
          ctx.fillStyle = T < settle ? `rgba(110,240,255,${a * 0.8})` : `rgba(235,252,255,${a})`;
          ctx.fillText(ch, cx - total / 2 + i * (fs * 0.62 + sp), H * 0.1 + fs * 0.5);
        }
        const sub = "MARIA  ·  SISTEMA ONLINE", shown = Math.floor(sub.length * seg(T, 4.0, 4.9));
        ctx.font = `600 ${Math.round(12 * dpr)}px Consolas, ui-monospace, monospace`;
        ctx.fillStyle = `rgba(127,240,196,${0.9 * words})`;
        ctx.fillText(sub.slice(0, shown).split("").join(" "), cx, H * 0.1 + fs * 1.3);

        // the log, bottom left
        ctx.textAlign = "left"; ctx.font = `${Math.round(11.5 * dpr)}px Consolas, ui-monospace, monospace`;
        const L = lines(), x0 = W * 0.05, y0 = H - (L.length + 2) * 19 * dpr;
        L.forEach((line, i) => {
          const k = seg(T, 3.1 + i * 0.36, 3.1 + i * 0.36 + 0.42);
          if (k <= 0) return;
          const txt = line.slice(0, Math.ceil(line.length * k));
          ctx.fillStyle = `rgba(95,140,149,${words})`; ctx.fillText(String(i + 1).padStart(2, "0"), x0, y0 + i * 19 * dpr);
          ctx.fillStyle = `rgba(200,240,248,${0.85 * words})`; ctx.fillText(txt, x0 + 30 * dpr, y0 + i * 19 * dpr);
          if (k >= 1) { ctx.fillStyle = `rgba(127,240,196,${words})`; ctx.fillText("✓", x0 + 30 * dpr + ctx.measureText(line).width + 10 * dpr, y0 + i * 19 * dpr); }
        });

        // gauges, right
        gauges.forEach((g, i) => {
          const k = outC(seg(T, 3.3 + i * 0.3, 4.6 + i * 0.3)), gx = W * 0.92, gy = H * 0.3 + i * 74 * dpr, r = 24 * dpr;
          ctx.lineWidth = 3 * dpr;
          ctx.strokeStyle = `rgba(110,240,255,${0.15 * words})`; ctx.beginPath(); ctx.arc(gx, gy, r, 0, Math.PI * 2); ctx.stroke();
          ctx.strokeStyle = `rgba(110,240,255,${0.9 * words})`; ctx.beginPath();
          ctx.arc(gx, gy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * k); ctx.stroke();
          ctx.textAlign = "center"; ctx.fillStyle = `rgba(235,252,255,${words})`;
          ctx.fillText(`${Math.round(100 * k)}`, gx, gy + 1);
          ctx.textAlign = "right"; ctx.fillStyle = `rgba(143,227,216,${0.8 * words})`;
          ctx.fillText(g, gx - r - 12 * dpr, gy + 1);
        });
        ctx.textAlign = "left";
      }
      // corner brackets of the screen, like a visor
      const vis = seg(T, 2.7, 3.3) * (1 - fold);
      if (vis > 0) {
        ctx.strokeStyle = `rgba(110,240,255,${0.5 * vis})`; ctx.lineWidth = 2 * dpr;
        const m = 26 * dpr, l = 46 * dpr;
        for (const [x, y, sx, sy] of [[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]]) {
          ctx.beginPath(); ctx.moveTo(x, y + sy * l); ctx.lineTo(x, y); ctx.lineTo(x + sx * l, y); ctx.stroke();
        }
      }
    };

    // start only when the window is really on screen (a slow PC paints the app window a moment late)
    const begin = () => {
      if (document.visibilityState !== "visible") return;
      document.removeEventListener("visibilitychange", begin);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        sound = playIntro(); stopSound.current = sound; raf = requestAnimationFrame(draw);
      }));
    };
    document.addEventListener("visibilitychange", begin);
    begin();
    const skip = (e: KeyboardEvent) => { if (!e.repeat) finish(true); };
    addEventListener("keydown", skip);
    return () => {
      cancelAnimationFrame(raf); removeEventListener("resize", resize); removeEventListener("keydown", skip);
      document.removeEventListener("visibilitychange", begin);
      if (!finished.current) sound?.();
    };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  if (gone) return null;
  return <div className={`intro${leaving ? " leaving" : ""}`} onClick={() => finish(true)} role="presentation">
    <canvas ref={canvas} className="intro-canvas" />
    <span className="intro-skip">clic o un tasto per saltare</span>
  </div>;
}

export const INTRO_DONE_EVENT = DONE;
