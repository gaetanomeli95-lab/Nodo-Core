import { useEffect, useRef } from "react";

export type PresenceMode = "rest" | "listen" | "think" | "act" | "speak" | "approve" | "reconnect";

/** Voice deformation uses measured PCM energy. Other states have slow ambient motion. */
export function Presence({ mode, sampleLevel }: {
  mode: PresenceMode; sampleLevel: () => { input: number; output: number };
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef({ mode, sampleLevel });
  latest.current = { mode, sampleLevel };
  useEffect(() => {
    const el = canvas.current!;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0, energy = 0, previous = 0;
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (document.hidden || now - previous < 32) return;
      previous = now;
      const { width: w, height: h } = el.getBoundingClientRect();
      const dpr = Math.min(devicePixelRatio || 1, 2);
      if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) {
        el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const { mode: m, sampleLevel: read } = latest.current;
      const levels = read();
      const target = Math.min(1, (m === "speak" ? levels.output : m === "listen" ? levels.input : 0) * 9);
      energy += (target - energy) * (target > energy ? .45 : .1);
      const t = reduced.matches ? 0 : now * .0002;
      const active = m === "think" || m === "act" || m === "reconnect";
      const grad = ctx.createLinearGradient(w * .1, 0, w * .9, 0);
      grad.addColorStop(0, "rgba(205,220,219,0)");
      grad.addColorStop(.28, "rgba(160,187,186,.5)");
      grad.addColorStop(.52, m === "approve" ? "rgba(235,183,121,.95)" : "rgba(245,236,213,.9)");
      grad.addColorStop(.74, "rgba(148,175,174,.5)");
      grad.addColorStop(1, "rgba(205,220,219,0)");
      ctx.strokeStyle = grad;
      const strength = reduced.matches ? 0 : energy;
      for (let j = 0; j < 48; j++) {
        const layer = j / 47;
        ctx.globalAlpha = .22 + .42 * Math.sin(layer * Math.PI);
        ctx.lineWidth = .65;
        ctx.beginPath();
        for (let i = 0; i <= 180; i++) {
          const u = i / 180;
          const envelope = Math.sin(Math.PI * u) ** 2;
          const twist = Math.sin(u * Math.PI * (active ? 3 : 2) + layer * 2.7 + t);
          const fold = Math.sin(u * Math.PI * 4 - layer * 2.1 - t * .6);
          const spread = (layer - .5) * h * .43;
          const y = h * .5 + envelope * (spread * Math.cos(u * 4.8 + t * .3)
            + twist * h * (.13 + strength * .17) + fold * h * .045);
          const x = w * (.04 + .92 * u);
          if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);
  return <canvas ref={canvas} className={`presence-ribbon ${mode}`} aria-hidden="true" />;
}
