import { useEffect, useRef, useState } from "react";
import { get } from "../../api";
import { calm, perf, watchFrames } from "../../motion";
import type { PresenceMode } from "../Presence";

/** The cockpit around Maria («grafica del 2492», 19/10): a light floor running to the horizon, a scanner that
 *  passes over the room, the frame of a ship's console and its readouts. The readouts only show measured facts:
 *  the model that really answered last and how long it took, today's answers and errors, how long NODO has been
 *  on, the jobs running now, and the real frame rate of this PC. Everything here is CSS transforms on the GPU,
 *  so the shop laptop pays almost nothing for it. */

type Hud = { brain: { provider: string; model: string; ms: number; ok: boolean; at: string } | null;
  runs: number; errors: number; avg_ms: number; uptime_s: number | null; jobs: number };

const NAMES: Record<string, string> = { "github-models": "GitHub", nvidia: "NVIDIA", groq: "Groq", gemini: "Gemini",
  "gemini-lite": "Gemini Lite", "claude-subscription": "Claude", "chatgpt-subscription": "ChatGPT", ollama: "Locale" };
const secs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1).replace(".", ",")} s` : `${ms} ms`);
const since = (s: number) => (s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h ${String(Math.floor(s % 3600 / 60)).padStart(2, "0")} min`);
const STATE: Record<PresenceMode, string> = { rest: "PRONTA", listen: "ASCOLTO", think: "ELABORO", act: "AL LAVORO",
  speak: "PARLO", approve: "ATTENDO TE", reconnect: "RICONNETTO" };

export function HoloHud({ mode, connected }: { mode: PresenceMode; connected: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  const [hud, setHud] = useState<Hud | null>(null);
  const [fps, setFps] = useState(0);
  const [low, setLow] = useState(perf.low);
  const fetched = useRef(Date.now());
  const [, tick] = useState(0);

  useEffect(() => {                                       // readouts: every 20 s (40 s on a small PC)
    let alive = true, timer = 0;
    const load = async () => {
      if (!document.hidden) {
        try { const r = await get<Hud>("/api/v1/hud"); if (alive) { fetched.current = Date.now(); setHud(r); } } catch { /* the frame stays */ }
      }
      if (alive) timer = window.setTimeout(load, perf.low ? 40000 : 20000);
    };
    void load();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  useEffect(() => {                                       // the real frame rate, and the switch to lite if it drops
    watchFrames();
    const t = setInterval(() => { setFps(perf.fps); tick((n) => n + 1); }, 2000);
    const onPerf = () => setLow(true);
    window.addEventListener("nodo:perf", onPerf);
    return () => { clearInterval(t); window.removeEventListener("nodo:perf", onPerf); };
  }, []);

  useEffect(() => {                                       // depth: the layers follow the hand a little
    if (calm) return;
    let raf = 0, px = 0, py = 0, x = 0, y = 0;
    const move = (e: PointerEvent) => { px = e.clientX / innerWidth - 0.5; py = e.clientY / innerHeight - 0.5;
      if (!raf) raf = requestAnimationFrame(apply); };
    const apply = () => {
      raf = 0;
      x += (px - x) * 0.35; y += (py - y) * 0.35;
      root.current?.style.setProperty("--px", x.toFixed(3));
      root.current?.style.setProperty("--py", y.toFixed(3));
      if (Math.abs(px - x) + Math.abs(py - y) > 0.002) raf = requestAnimationFrame(apply);
    };
    addEventListener("pointermove", move, { passive: true });
    return () => { removeEventListener("pointermove", move); cancelAnimationFrame(raf); };
  }, []);

  const up = hud?.uptime_s != null ? hud.uptime_s + Math.floor((Date.now() - fetched.current) / 1000) : null;
  const brain = hud?.brain;
  return <div ref={root} className={`hud hud-${mode}${low ? " hud-low" : ""}`} aria-hidden="true">
    <div className="hud-floor"><div className="hud-floor-grid" /></div><div className="hud-horizon" />
    <div className="hud-sweep" />
    <div className="hud-scanlines" />
    <div className="hud-frame">
      <i className="hud-c tl" /><i className="hud-c tr" /><i className="hud-c bl" /><i className="hud-c br" />
      <i className="hud-ruler l" /><i className="hud-ruler r" />
      <span className="hud-tag">NODO · PLANCIA DI COMANDO</span>
      <span className="hud-tag hud-tag-r">{connected ? `SISTEMA ${STATE[mode]}` : "COLLEGAMENTO…"}<b className="hud-blink" /></span>
    </div>
    <dl className="hud-read">
      <div><dt>CERVELLO</dt><dd>{brain ? `${NAMES[brain.provider] || brain.provider} · ${brain.model.split("/").pop()}` : "—"}</dd></div>
      <div><dt>ULTIMA RISPOSTA</dt><dd className={brain && !brain.ok ? "bad" : ""}>{brain ? `${secs(brain.ms)}${brain.ok ? "" : " · errore"}` : "—"}</dd></div>
      <div><dt>OGGI</dt><dd>{hud ? `${hud.runs} risposte${hud.errors ? ` · ${hud.errors} errori` : ""}` : "—"}</dd></div>
      <div><dt>ACCESA DA</dt><dd>{up != null ? since(up) : "—"}</dd></div>
      <div><dt>LAVORI</dt><dd>{hud ? (hud.jobs ? `${hud.jobs} in corso` : "nessuno") : "—"}</dd></div>
      <div><dt>FOTOGRAMMI</dt><dd className={fps && fps < 24 ? "bad" : ""}>{fps ? `${fps}/s${low ? " · leggera" : ""}` : "—"}</dd></div>
    </dl>
  </div>;
}
