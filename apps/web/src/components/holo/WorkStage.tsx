import { useEffect, useRef, useState } from "react";
import { calm, lite } from "../../motion";

/** While Maria does something long and worth the wait (an image, a document, a web search, reading old chats),
 *  you SEE her working instead of reading «ci sto pensando»: an image develops out of light inside a frame, a
 *  document prints itself line by line, a radar finds sources. A clock counts the seconds and a bar moves
 *  towards the usual time, so a minute does not feel like a crash. */

export const SLOW_TOOLS: Record<string, { kind: "image" | "doc" | "web" | "read" | "sites"; label: string; usual: number }> = {
  generate_image: { kind: "image", label: "Sto disegnando", usual: 45 },
  create_document: { kind: "doc", label: "Sto preparando il documento", usual: 25 },
  create_file: { kind: "doc", label: "Sto preparando il file", usual: 10 },
  save_to_desktop: { kind: "doc", label: "Lo salvo sul desktop", usual: 6 },
  web_search: { kind: "web", label: "Cerco sul web", usual: 15 },
  fetch_url: { kind: "web", label: "Leggo la pagina", usual: 15 },
  read_photo: { kind: "read", label: "Guardo la foto", usual: 15 },
  learn_old_chats: { kind: "read", label: "Leggo le vecchie chat", usual: 40 },
  search_knowledge: { kind: "read", label: "Cerco nelle vecchie conversazioni", usual: 8 },
  check_sites: { kind: "sites", label: "Controllo i siti", usual: 30 },
  sync_sites: { kind: "sites", label: "Importo i siti", usual: 30 },
};
/** How long the panel waits before calling it stuck (seconds), per tool. */
export const patienceFor = (tool: string | null) => Math.max(75, ((tool && SLOW_TOOLS[tool]?.usual) || 0) * 4 + 30);

export type Work = { tool: string; args: Record<string, unknown>; since: number };

function subject(w: Work): string {
  const a = w.args || {};
  return String(a.description || a.query || a.name || a.title || a.url || a.what || "").slice(0, 140);
}

function Develop() {                     // an image coming out of light: soft colour fields, sharpening over time
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current!, ctx = c.getContext("2d");
    if (!ctx) return;
    const W = (c.width = lite ? 160 : 240), H = (c.height = lite ? 120 : 180);
    const blobs = Array.from({ length: 9 }, () => ({ x: Math.random() * W, y: Math.random() * H, r: 30 + Math.random() * 60,
      vx: (Math.random() - .5) * 12, vy: (Math.random() - .5) * 12, h: Math.floor(160 + Math.random() * 140) }));
    let raf = 0, last = performance.now();
    const t0 = last;
    const draw = (t: number) => {
      raf = requestAnimationFrame(draw);
      if (t - last < (lite ? 70 : 40)) return;
      const dt = (t - last) / 1000; last = t;
      const age = (t - t0) / 1000;
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = "rgba(2,8,12,0.18)"; ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      for (const b of blobs) {
        if (!calm) { b.x += b.vx * dt; b.y += b.vy * dt; }
        if (b.x < 0 || b.x > W) b.vx *= -1;
        if (b.y < 0 || b.y > H) b.vy *= -1;
        const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r);
        g.addColorStop(0, `hsla(${b.h},80%,60%,${Math.min(0.35, 0.08 + age / 120)})`); g.addColorStop(1, "hsla(0,0%,0%,0)");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";           // grain: the picture is still «developing»
      for (let i = 0; i < (lite ? 60 : 160); i++) {
        ctx.fillStyle = `rgba(200,250,255,${Math.random() * 0.18})`;
        ctx.fillRect(Math.random() * W, Math.random() * H, 1, 1);
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className="work-develop" />;
}

export function WorkStage({ work }: { work: Work }) {
  const spec = SLOW_TOOLS[work.tool] || { kind: "read" as const, label: "Ci lavoro", usual: 20 };
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(t); }, []);
  const secs = Math.max(0, (now - work.since) / 1000);
  const progress = 1 - Math.exp(-secs / (spec.usual * 0.7));       // approaches the end, never claims to be done
  const what = subject(work);
  return <section className={`work-stage ${spec.kind}`} role="status" aria-live="polite">
    <div className="work-visual">
      {spec.kind === "image" && <div className="work-frame"><Develop /><i className="work-scan" />
        <i className="c tl" /><i className="c tr" /><i className="c bl" /><i className="c br" /></div>}
      {spec.kind === "doc" && <div className="work-paper">{Array.from({ length: 9 }, (_, i) =>
        <i key={i} style={{ animationDelay: `${i * 0.45}s`, width: `${55 + ((i * 37) % 40)}%` }} />)}</div>}
      {spec.kind === "web" && <div className="work-radar"><i className="sweep" />{Array.from({ length: 7 }, (_, i) =>
        <b key={i} style={{ left: `${20 + ((i * 53) % 60)}%`, top: `${15 + ((i * 31) % 70)}%`, animationDelay: `${0.6 + i * 0.7}s` }} />)}</div>}
      {(spec.kind === "read" || spec.kind === "sites") && <div className="work-orbit"><i /><i /><i /><b /></div>}
    </div>
    <div className="work-text">
      <span className="sheet-kicker">AL LAVORO</span>
      <h3>{spec.label}{what ? "…" : ""}</h3>
      {what && <p>«{what}»</p>}
      <div className="work-bar"><i style={{ width: `${Math.round(progress * 100)}%` }} /></div>
      <small>{Math.floor(secs)} s{secs > spec.usual * 1.5 ? " · ci vuole un po' più del solito, continuo" : ` · di solito ${spec.usual} s`}</small>
    </div>
  </section>;
}
