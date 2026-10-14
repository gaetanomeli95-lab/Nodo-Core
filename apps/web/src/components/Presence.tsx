import { useEffect, useRef } from "react";
import { calm } from "../motion";

export type PresenceMode = "rest" | "listen" | "think" | "act" | "speak" | "approve" | "reconnect";
type Bins = { input: Uint8Array<ArrayBuffer> | null; output: Uint8Array<ArrayBuffer> | null };

/** Colour of each state: [core, glow, accent] as r,g,b. */
const PALETTE: Record<PresenceMode, [number[], number[], number[]]> = {
  rest:      [[190, 236, 226], [110, 200, 190], [150, 180, 255]],
  listen:    [[150, 235, 255], [60, 190, 255], [120, 255, 220]],
  think:     [[205, 185, 255], [140, 110, 255], [90, 200, 255]],
  act:       [[160, 255, 215], [60, 220, 170], [120, 170, 255]],
  speak:     [[255, 228, 170], [255, 170, 90], [120, 230, 220]],
  approve:   [[255, 214, 150], [240, 150, 70], [255, 230, 190]],
  reconnect: [[200, 200, 210], [140, 150, 170], [255, 170, 120]],
};
const rgba = (c: number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const mix = (a: number[], b: number[], k: number) => a.map((v, i) => v + (b[i] - v) * k);

/** NODO's presence: a living core with a ring that dances on the real frequencies of the voice (yours while you
 *  speak, NODO's while it answers), sound waves running across the stage and particles in orbit. */
export function Presence({ mode, sampleLevel, spectrum, bodyless = false }: {
  mode: PresenceMode; sampleLevel: () => { input: number; output: number }; spectrum?: () => Bins;
  /** true when the WebGL body (HoloCore) draws the core: here only waves and HUD rings */
  bodyless?: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef({ mode, sampleLevel, spectrum, bodyless });
  latest.current = { mode, sampleLevel, spectrum, bodyless };

  useEffect(() => {
    const el = canvas.current!;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const reduced = { matches: calm };
    const N = 96;
    const bars = new Float32Array(N);                 // smoothed ring heights 0..1
    const parts = Array.from({ length: 70 }, (_, i) => ({
      a: Math.random() * Math.PI * 2, r: 1.25 + Math.random() * 1.1, s: (0.15 + Math.random() * 0.5) * (i % 2 ? 1 : -1),
      z: Math.random(),
    }));
    let colour = PALETTE.rest.map((c) => [...c]);
    let frame = 0, energy = 0, previous = 0, spin = 0, bands = [0, 0, 0];

    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (document.hidden || now - previous < 30) return;
      const dt = Math.min(0.08, (now - (previous || now)) / 1000);
      previous = now;
      const { width: w, height: h } = el.getBoundingClientRect();
      const dpr = Math.min(devicePixelRatio || 1, 2);
      if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) {
        el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const { mode: m, sampleLevel: read, spectrum: spec } = latest.current;
      const still = reduced.matches;

      // --- signal: energy + three bands (low / mid / high) from the live spectrum
      const lv = read();
      const raw = m === "speak" ? lv.output : m === "listen" ? lv.input : 0;
      const target = Math.min(1, raw * 9);
      energy += (target - energy) * (target > energy ? 0.5 : 0.12);
      const bins = spec?.();
      const src = m === "speak" ? bins?.output : m === "listen" ? bins?.input : null;
      const nb = [0, 0, 0];
      for (let i = 0; i < N; i++) {
        let v: number;
        if (src) {
          const half = i < N / 2 ? i : N - 1 - i;                        // mirrored: symmetric ring
          const idx = Math.min(src.length - 1, Math.floor((half / (N / 2)) ** 1.6 * src.length * 0.7) + 1);
          v = src[idx] / 255;
        } else {
          const t = now * 0.001;
          const idle = m === "think" || m === "act" ? 0.32 : m === "reconnect" ? 0.12 : 0.16;
          v = idle * (0.55 + 0.45 * Math.sin(i * 0.42 + t * (m === "think" ? 3.2 : 1.1)) * Math.sin(i * 0.13 - t * 0.7))
            + energy * 0.5;
        }
        v = Math.max(0, v);
        bars[i] += (v - bars[i]) * (v > bars[i] ? 0.55 : 0.14);
        nb[i < N * 0.15 || i > N * 0.85 ? 0 : i < N * 0.33 || i > N * 0.67 ? 1 : 2] += bars[i];
      }
      bands = bands.map((b, k) => b + ((nb[k] / (N / 3)) - b) * 0.25);

      // --- colours glide between states
      const pal = PALETTE[m];
      colour = colour.map((c, k) => mix(c, pal[k], 0.08));
      const [core, glow, accent] = colour;
      spin += dt * (m === "think" ? 0.9 : m === "act" ? 1.4 : m === "reconnect" ? 0.3 : 0.18) * (still ? 0 : 1);

      const cx = w / 2, cy = h / 2;
      const R = Math.min(w * 0.18, h * 0.3);
      const pulse = still ? 0 : Math.sin(now * 0.0016) * 0.03;
      const r0 = R * (0.62 + pulse + energy * 0.16 + bands[0] * 0.12);

      ctx.globalCompositeOperation = "lighter";

      // sound waves across the whole stage (behind the core)
      const waves = 4;
      for (let k = 0; k < waves; k++) {
        const amp = h * (0.035 + bands[Math.min(2, k)] * 0.22 + energy * 0.12) * (1 - k * 0.16);
        const freq = 2.2 + k * 1.3;
        const speed = (still ? 0 : now * 0.0012) * (1 + k * 0.35) * (m === "speak" || m === "listen" ? 1.8 : 1);
        ctx.beginPath();
        for (let i = 0; i <= 200; i++) {
          const u = i / 200;
          const env = Math.sin(Math.PI * u) ** 1.6;
          const y = cy + env * amp * Math.sin(u * Math.PI * freq * 2 + speed + k)
            * (0.75 + 0.25 * Math.sin(u * 13 + speed * 0.7));
          const x = w * u;
          if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        const g = ctx.createLinearGradient(0, 0, w, 0);
        const col = k % 2 ? accent : glow;
        g.addColorStop(0, rgba(col, 0)); g.addColorStop(0.35, rgba(col, 0.35 - k * 0.05));
        g.addColorStop(0.5, rgba(core, 0.6 - k * 0.08)); g.addColorStop(0.65, rgba(col, 0.35 - k * 0.05));
        g.addColorStop(1, rgba(col, 0));
        ctx.strokeStyle = g; ctx.lineWidth = k ? 1 : 1.6;
        ctx.stroke();
      }

      // HUD rings and rotating arcs
      ctx.lineWidth = 1;
      for (let k = 0; k < 3; k++) {
        ctx.strokeStyle = rgba(glow, 0.09 - k * 0.02);
        ctx.beginPath(); ctx.arc(cx, cy, R * (1.55 + k * 0.32), 0, Math.PI * 2); ctx.stroke();
      }
      for (let k = 0; k < 3; k++) {
        const a0 = spin * (k % 2 ? -1.3 : 1) + k * 2.1;
        ctx.strokeStyle = rgba(k === 1 ? accent : glow, 0.35);
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.arc(cx, cy, R * (1.55 + k * 0.32), a0, a0 + 0.5 + k * 0.25); ctx.stroke();
      }
      for (let i = 0; i < 60; i++) {                                  // tick marks
        const a = (i / 60) * Math.PI * 2 + spin * 0.25;
        const r1 = R * 2.2, r2 = r1 + (i % 5 ? 3 : 8);
        ctx.strokeStyle = rgba(glow, i % 5 ? 0.12 : 0.28);
        ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
        ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2); ctx.stroke();
      }

      if (latest.current.bodyless) { ctx.globalCompositeOperation = "source-over"; return; }
      // the spectrum ring
      for (let i = 0; i < N; i++) {
        const a = (i / N) * Math.PI * 2 - Math.PI / 2 + spin * 0.12;
        const len = R * (0.06 + bars[i] * 0.75);
        const rIn = r0 * 1.18;
        const x1 = cx + Math.cos(a) * rIn, y1 = cy + Math.sin(a) * rIn;
        const x2 = cx + Math.cos(a) * (rIn + len), y2 = cy + Math.sin(a) * (rIn + len);
        const g = ctx.createLinearGradient(x1, y1, x2, y2);
        g.addColorStop(0, rgba(core, 0.85)); g.addColorStop(1, rgba(accent, 0.05));
        ctx.strokeStyle = g; ctx.lineWidth = Math.max(1.2, (Math.PI * 2 * rIn) / N * 0.55);
        ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      }

      // particles in orbit
      for (const p of parts) {
        if (!still) p.a += p.s * dt * (m === "think" ? 2.4 : m === "act" ? 3 : 1) * (1 + energy * 2);
        const rr = R * p.r * (1 + energy * 0.25 * p.z);
        const x = cx + Math.cos(p.a) * rr, y = cy + Math.sin(p.a) * rr * 0.92;
        ctx.fillStyle = rgba(p.z > 0.6 ? accent : core, 0.25 + p.z * 0.5);
        ctx.beginPath(); ctx.arc(x, y, 0.6 + p.z * 1.5, 0, Math.PI * 2); ctx.fill();
      }

      // the core
      const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, r0 * 2.6);
      halo.addColorStop(0, rgba(glow, 0.38 + energy * 0.3)); halo.addColorStop(0.45, rgba(glow, 0.1));
      halo.addColorStop(1, rgba(glow, 0));
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(cx, cy, r0 * 2.6, 0, Math.PI * 2); ctx.fill();
      ctx.globalCompositeOperation = "source-over";
      const body = ctx.createRadialGradient(cx - r0 * 0.3, cy - r0 * 0.35, r0 * 0.05, cx, cy, r0);
      body.addColorStop(0, rgba([255, 255, 255], 0.95)); body.addColorStop(0.35, rgba(core, 0.9));
      body.addColorStop(0.8, rgba(glow, 0.55)); body.addColorStop(1, rgba(glow, 0.12));
      ctx.fillStyle = body;
      ctx.beginPath();
      for (let i = 0; i <= 64; i++) {                                 // a living, slightly liquid outline
        const a = (i / 64) * Math.PI * 2;
        const wob = still ? 0 : Math.sin(a * 3 + now * 0.002) * 0.025 + Math.sin(a * 5 - now * 0.003) * 0.02 * (1 + energy * 3)
          + bars[Math.floor((i / 64) * N) % N] * 0.07;
        const rr = r0 * (1 + wob);
        const x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr;
        if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.fill();
      ctx.strokeStyle = rgba(core, 0.5); ctx.lineWidth = 1; ctx.stroke();
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);
  return <canvas ref={canvas} className={`presence-core ${mode}`} aria-hidden="true" />;
}
