import { useEffect, useRef } from "react";
import { calm, canvasDpr, lite } from "../../motion";

/** The living background: a slow neural field (nodes that drift and link when close) over a deep starfield.
 *  Very light on the GPU (30 fps, pauses when the window is hidden), static with reduced motion. */
export function Cosmos() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = ref.current!;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const reduced = calm;
    let w = 0, h = 0, raf = 0, last = 0, checked = -1e9, covered = false;
    const stars = Array.from({ length: lite ? 90 : 160 }, () => ({ x: Math.random(), y: Math.random(), r: Math.random() * 1.1 + 0.2, t: Math.random() * 6 }));
    const nodes = Array.from({ length: lite ? 26 : 46 }, () => ({
      x: Math.random(), y: Math.random(), vx: (Math.random() - 0.5) * 0.00012, vy: (Math.random() - 0.5) * 0.00012,
    }));
    const size = () => {
      const dpr = canvasDpr();
      w = innerWidth; h = innerHeight;
      el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();
    addEventListener("resize", size);
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (document.hidden || now - last < (lite ? 80 : 33)) return;   // a background: 12 fps is plenty on a small PC
      if (now - checked > 1000) { checked = now; const b = document.querySelector(".bridge-gl"); covered = !!b && b.getClientRects().length > 0; }
      if (covered) { last = now; return; }                              // the bridge is drawing the sky: rest
      const dt = Math.min(64, now - (last || now));
      last = now;
      ctx.clearRect(0, 0, w, h);
      for (const s of stars) {
        const a = reduced ? 0.5 : 0.25 + 0.35 * Math.sin(now * 0.0008 + s.t);
        ctx.fillStyle = `rgba(200,230,255,${a})`;
        ctx.fillRect(s.x * w, s.y * h, s.r, s.r);
      }
      for (const n of nodes) {
        if (!reduced) { n.x += n.vx * dt; n.y += n.vy * dt; }
        if (n.x < -0.05) n.x = 1.05; if (n.x > 1.05) n.x = -0.05;
        if (n.y < -0.05) n.y = 1.05; if (n.y > 1.05) n.y = -0.05;
      }
      const max = Math.min(w, h) * 0.17;
      ctx.lineWidth = 0.6;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = (a.x - b.x) * w, dy = (a.y - b.y) * h, d = Math.hypot(dx, dy);
          if (d < max) {
            ctx.strokeStyle = `rgba(110,220,230,${0.12 * (1 - d / max)})`;
            ctx.beginPath(); ctx.moveTo(a.x * w, a.y * h); ctx.lineTo(b.x * w, b.y * h); ctx.stroke();
          }
        }
        ctx.fillStyle = "rgba(140,235,240,.35)";
        ctx.beginPath(); ctx.arc(a.x * w, a.y * h, 1.3, 0, Math.PI * 2); ctx.fill();
      }
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); removeEventListener("resize", size); };
  }, []);
  return <><div className="holo-aurora" aria-hidden="true" /><canvas ref={ref} className="holo-cosmos" aria-hidden="true" /></>;
}
