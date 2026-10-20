import { useEffect, useRef, useState, type ReactNode } from "react";
import { get } from "../../api";
import { perf } from "../../motion";
import type { PresenceMode } from "../Presence";
import { bridgeStats } from "./BridgeGL";
import type { Thought } from "./useMindFeed";

/** The HTML half of the bridge: the rings of the dial around Maria (aligned with the WebGL sphere through
 *  --bridge-r / --bridge-cy), and the two console panels leaning towards you. Left: ship's clock and telemetry,
 *  measured facts only. Right: the lavagna and her latest real thoughts. */

type Hud = { brain: { provider: string; model: string; ms: number; ok: boolean } | null;
  runs: number; errors: number; avg_ms: number; uptime_s: number | null; jobs: number };
type Level = () => { input: number; output: number };

const NAMES: Record<string, string> = { "github-models": "GitHub", nvidia: "NVIDIA", groq: "Groq", gemini: "Gemini",
  "gemini-lite": "Gemini Lite", "claude-subscription": "Claude", "chatgpt-subscription": "ChatGPT", ollama: "Locale" };
const secs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1).replace(".", ",")} s` : `${ms} ms`);
const since = (s: number) => (s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h ${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}`);
const DAYS = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
const MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];
const QUALITY = ["minima", "leggera", "piena"];
const num = (n?: number) => (n ?? 0).toLocaleString("it-IT");

export function BridgeRings({ mode }: { mode: PresenceMode }) {
  const ticks = [];
  for (let i = 0; i < 90; i++) {
    const a = i / 90 * Math.PI * 2, r1 = 186, r2 = i % 6 ? 192 : 200;
    ticks.push(<line key={i} x1={250 + Math.cos(a) * r1} y1={250 + Math.sin(a) * r1} x2={250 + Math.cos(a) * r2} y2={250 + Math.sin(a) * r2}
      stroke="var(--state)" strokeOpacity={i % 6 ? 0.35 : 0.8} strokeWidth={1} />);
  }
  const blades = mode === "think" || mode === "act" ? "4s" : mode === "speak" ? "14s" : "24s";
  return <div className="bridge-rings" aria-hidden="true">
    <svg viewBox="0 0 500 500">
      <g className="spin" style={{ ["--d" as string]: "120s" }}>
        <circle cx="250" cy="250" r="238" fill="none" stroke="var(--state)" strokeOpacity=".22" strokeWidth="1" strokeDasharray="2 7" />
        {["M250 12 a238 238 0 0 1 60 7.7", "M488 250 a238 238 0 0 1 -7.7 60", "M250 488 a238 238 0 0 1 -60 -7.7", "M12 250 a238 238 0 0 1 7.7 -60"]
          .map((d) => <path key={d} d={d} fill="none" stroke="var(--amber)" strokeWidth="2.5" />)}
        <text x="250" y="2" textAnchor="middle">N · 000</text><text x="502" y="253" textAnchor="start">090</text>
        <text x="250" y="510" textAnchor="middle">180</text><text x="-2" y="253" textAnchor="end">270</text>
      </g>
      <g className="spin rev" style={{ ["--d" as string]: blades }}>
        <circle cx="250" cy="250" r="214" fill="none" stroke="var(--state2)" strokeOpacity=".7" strokeWidth="3" strokeDasharray="120 104.1" />
      </g>
      <g className="spin" style={{ ["--d" as string]: "80s" }}>{ticks}</g>
    </svg>
  </div>;
}

export function BridgeLeft({ stats, sampleLevel }: { stats: Record<string, number>; sampleLevel: Level }) {
  const [now, setNow] = useState(() => new Date());
  const [hud, setHud] = useState<Hud | null>(null);
  const fetched = useRef(Date.now());
  const [, setTick] = useState(0);
  const meter = useRef<HTMLElement>(null);
  const meterTxt = useRef<HTMLElement>(null);
  useEffect(() => { const t = setInterval(() => { setNow(new Date()); }, 1000); return () => clearInterval(t); }, []);
  useEffect(() => {
    let alive = true, timer = 0;
    const load = async () => {
      if (!document.hidden) {
        try { const r = await get<Hud>("/api/v1/hud"); if (alive) { fetched.current = Date.now(); setHud(r); } } catch { /* stays */ }
      }
      if (alive) timer = window.setTimeout(load, perf.low ? 40000 : 20000);
    };
    void load();
    const t = setInterval(() => setTick((n) => n + 1), 2000);
    return () => { alive = false; clearTimeout(timer); clearInterval(t); };
  }, []);
  useEffect(() => {                                   // the voice meter moves with the real sound, no re-render
    let raf = 0, lv = 0, last = 0;
    const step = (t: number) => {
      raf = requestAnimationFrame(step);
      if (t - last < 80 || document.hidden) return;
      last = t;
      const s = sampleLevel();
      lv += (Math.min(1, Math.max(s.input, s.output) * 1.6) - lv) * 0.5;
      if (meter.current) meter.current.style.width = `${Math.round(lv * 100)}%`;
      if (meterTxt.current) meterTxt.current.textContent = `${Math.round(lv * 100)}%`;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [sampleLevel]);
  const t = now.toTimeString();
  const brain = hud?.brain;
  const up = hud?.uptime_s != null ? hud.uptime_s + Math.floor((Date.now() - fetched.current) / 1000) : null;
  return <section className="bridge-side l ch" aria-label="Telemetria"><div className="in">
    <div className="lbl">Ora di bordo<em>Palermo</em></div>
    <div className="bclock">{t.slice(0, 5)}<s>{t.slice(6, 8)}</s></div>
    <div className="bdate">{DAYS[now.getDay()]} {now.getDate()} {MONTHS[now.getMonth()]}</div>
    <div className="lbl">Telemetria<em>misurata</em></div>
    <dl className="read">
      <div><dt>Cervello</dt><dd className="m">{brain ? `${NAMES[brain.provider] || brain.provider} · ${brain.model.split("/").pop()}` : "—"}</dd></div>
      <div><dt>Risposta</dt><dd className={brain && !brain.ok ? "bad" : "a"}>{brain ? `${secs(brain.ms)}${brain.ok ? "" : " · errore"}` : "—"}</dd></div>
      <div><dt>Oggi</dt><dd>{hud ? `${hud.runs} risposte${hud.errors ? ` · ${hud.errors} errori` : ""}` : "—"}</dd></div>
      <div><dt>Accesa da</dt><dd>{up != null ? since(up) : "—"}{hud?.jobs ? <b className="a"> · {hud.jobs} lavori</b> : null}</dd></div>
    </dl>
    <dl className="read">
      <div><dt>Ricordi · clienti</dt><dd><b className="p">{num(stats.ricordi)}</b> · <b className="a">{num(stats.clienti)}</b></dd></div>
    </dl>
    <div className="lbl">Voce<em ref={meterTxt}>0%</em></div>
    <div className="meter"><b ref={meter} /></div>
    <dl className="read">
      <div><dt>Questo PC</dt><dd className={bridgeStats.fps && bridgeStats.fps < 24 ? "bad" : "m"}>{bridgeStats.fps ? `${bridgeStats.fps} fps · ${QUALITY[bridgeStats.tier]}` : "—"}</dd></div>
    </dl>
  </div></section>;
}

export function BridgeRight({ log, lit, children }: { log: Thought[]; lit: string[]; children?: ReactNode }) {
  return <section className="bridge-side r ch" aria-label="Lavagna e pensieri"><div className="in">
    {children}
    {!!lit.length && <><div className="lbl">Sto ricordando</div>
      <div className="lit">{lit.map((l) => <span key={l}>{l}</span>)}</div></>}
    <div className="lbl">Ultimi pensieri<em>veri</em></div>
    {log.length ? <ul className="blog">{log.map((l) => <li key={l.t + l.text} className={l.kind}><time>{l.t}</time><span>{l.text}</span></li>)}</ul>
      : <p className="bempty">Quando lavora, ascolta o ricorda qualcosa, lo vedi scritto qui.</p>}
  </div></section>;
}
