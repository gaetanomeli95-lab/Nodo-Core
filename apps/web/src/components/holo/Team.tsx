import { useEffect, useRef, useState } from "react";
import { get } from "../../api";

/** «La squadra»: Maria's agents as holographic colleagues on glowing pedestals around her, in a room that turns
 *  slowly. Each one is a real slice of NODO with live numbers (what it did today, the last thing, what waits).
 *  A pedestal lights up and beams when that agent is working. Click one: its card. Canvas 2D, light on any PC. */

type Agent = { id: string; name: string; color: string; replaces: string; does: string; status: string; today: number;
  last: { what: string; ok: boolean; at: string } | null; facts: string[]; business?: string };
type TeamState = { leader: { name: string; role: string; today: number }; agents: Agent[] };

const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? "adesso" : s < 3600 ? `${Math.round(s / 60)} min fa` : s < 86400 ? `${Math.round(s / 3600)} ore fa`
    : `${Math.round(s / 86400)} giorni fa`;
};
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

export function TeamView() {
  const [team, setTeam] = useState<TeamState | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  const data = useRef<TeamState | null>(null);
  const hover = useRef<string | null>(null);
  const spots = useRef<{ id: string; x: number; y: number; r: number }[]>([]);
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef({ rot: 0, drag: false, x: 0 });
  const pickRef = useRef(pick);
  pickRef.current = pick;

  useEffect(() => {
    let alive = true;
    const load = () => get<TeamState>("/api/v1/team").then((t) => { if (alive) { setTeam(t); data.current = t; } }).catch(() => undefined);
    load();
    const t = setInterval(load, 8000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    const c = canvas.current; if (!c) return;
    const ctx = c.getContext("2d"); if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const fit = () => { const r = c.getBoundingClientRect(); c.width = r.width * dpr; c.height = r.height * dpr; };
    fit(); const ro = new ResizeObserver(fit); ro.observe(c);
    let raf = 0, last = 0;
    const born = performance.now();
    const draw = (t: number) => {
      raf = requestAnimationFrame(draw);
      if (t - last < 1000 / 40) return;
      const dt = Math.min(0.1, (t - last) / 1000); last = t;
      const v = view.current; if (!v.drag) v.rot += dt * 0.06;
      const W = c.width, H = c.height, cx = W / 2, cy = H * 0.6, R = Math.min(W * 0.38, H * 0.62);
      const tilt = 0.36, enter = Math.min(1, (t - born) / 1400), ease = 1 - Math.pow(1 - enter, 3);
      ctx.clearRect(0, 0, W, H);
      // the floor: concentric rings and a slow radar sweep
      ctx.save(); ctx.translate(cx, cy); ctx.scale(1, tilt);
      for (let k = 1; k <= 5; k++) {
        ctx.strokeStyle = `rgba(120,220,240,${0.05 + (k === 5 ? 0.12 : 0)})`; ctx.lineWidth = (k === 5 ? 2 : 1) * dpr;
        ctx.setLineDash(k % 2 ? [] : [3 * dpr, 9 * dpr]);
        ctx.beginPath(); ctx.arc(0, 0, R * 1.18 * (k / 5) * ease, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.setLineDash([]);
      const sw = (t / 2600) % (Math.PI * 2);
      const g = ctx.createConicGradient ? ctx.createConicGradient(sw, 0, 0) : null;
      if (g) { g.addColorStop(0, "rgba(120,230,255,.16)"); g.addColorStop(0.08, "rgba(120,230,255,0)"); g.addColorStop(1, "rgba(120,230,255,0)");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, R * 1.18 * ease, 0, Math.PI * 2); ctx.fill(); }
      ctx.restore();
      const st = data.current;
      const agents = st?.agents || [];
      const pts = agents.map((a, i) => {
        const ang = v.rot + (i / Math.max(1, agents.length)) * Math.PI * 2;
        const x = Math.cos(ang) * R * ease, z = Math.sin(ang);
        return { a, x: cx + x, y: cy + z * R * tilt * ease, depth: z, scale: 0.78 + (z + 1) * 0.16 };
      }).sort((p, q) => p.depth - q.depth);
      // Maria in the middle: a column of light, links of light to every busy colleague
      const pulse = 0.5 + Math.sin(t / 600) * 0.5;
      for (const p of pts) {
        const busy = p.a.status === "al lavoro", [r, g2, b] = rgb(p.a.color);
        ctx.strokeStyle = `rgba(${r},${g2},${b},${busy ? 0.55 : 0.1})`; ctx.lineWidth = (busy ? 2 : 1) * dpr;
        ctx.beginPath(); ctx.moveTo(cx, cy - 30 * dpr); ctx.lineTo(p.x, p.y - 40 * dpr * p.scale); ctx.stroke();
        if (busy) { const u = (t / 700) % 1; ctx.fillStyle = `rgba(255,255,255,.9)`; ctx.beginPath();
          ctx.arc(cx + (p.x - cx) * u, cy - 30 * dpr + (p.y - 40 * dpr * p.scale - cy + 30 * dpr) * u, 2.2 * dpr, 0, Math.PI * 2); ctx.fill(); }
      }
      const col = ctx.createLinearGradient(cx, cy - 170 * dpr, cx, cy);
      col.addColorStop(0, "rgba(255,226,170,0)"); col.addColorStop(1, `rgba(255,226,170,${0.35 + pulse * 0.2})`);
      ctx.fillStyle = col; ctx.fillRect(cx - 18 * dpr, cy - 170 * dpr, 36 * dpr, 170 * dpr);
      const core = ctx.createRadialGradient(cx, cy - 34 * dpr, 0, cx, cy - 34 * dpr, 46 * dpr);
      core.addColorStop(0, "rgba(255,246,225,1)"); core.addColorStop(0.25, "rgba(255,210,140,.75)"); core.addColorStop(1, "rgba(255,180,90,0)");
      ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx, cy - 34 * dpr, 46 * dpr, 0, Math.PI * 2); ctx.fill();
      ctx.save(); ctx.translate(cx, cy); ctx.scale(1, tilt);
      ctx.strokeStyle = `rgba(255,220,160,${0.4 + pulse * 0.3})`; ctx.lineWidth = 2 * dpr;
      ctx.beginPath(); ctx.arc(0, 0, 48 * dpr, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      // the colleagues: pedestal, beam, holographic figure, name
      const hit: typeof spots.current = [];
      for (const p of pts) {
        const s = p.scale * dpr, [r, g2, b] = rgb(p.a.color), busy = p.a.status === "al lavoro";
        const on = hover.current === p.a.id || pickRef.current === p.a.id;
        const alpha = 0.45 + (p.depth + 1) * 0.27;
        ctx.save(); ctx.translate(p.x, p.y); ctx.scale(1, tilt);
        const disc = ctx.createRadialGradient(0, 0, 0, 0, 0, 46 * s);
        disc.addColorStop(0, `rgba(${r},${g2},${b},${(busy ? 0.6 : 0.3) * alpha})`); disc.addColorStop(1, `rgba(${r},${g2},${b},0)`);
        ctx.fillStyle = disc; ctx.beginPath(); ctx.arc(0, 0, 46 * s, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = `rgba(${r},${g2},${b},${(on ? 1 : 0.7) * alpha})`; ctx.lineWidth = (on ? 2.4 : 1.4) * dpr;
        ctx.beginPath(); ctx.arc(0, 0, 34 * s, 0, Math.PI * 2); ctx.stroke();
        ctx.restore();
        if (busy) {
          const beam = ctx.createLinearGradient(p.x, p.y - 140 * s, p.x, p.y);
          beam.addColorStop(0, `rgba(${r},${g2},${b},0)`); beam.addColorStop(1, `rgba(${r},${g2},${b},${0.32 + pulse * 0.2})`);
          ctx.fillStyle = beam; ctx.fillRect(p.x - 26 * s, p.y - 140 * s, 52 * s, 140 * s);
        }
        // figure: shoulders + head, made of light with scanlines
        const fy = p.y - 8 * s, bob = Math.sin(t / 900 + p.depth * 3) * 2 * s;
        const body = ctx.createLinearGradient(p.x, fy - 70 * s, p.x, fy);
        body.addColorStop(0, `rgba(${r},${g2},${b},${0.95 * alpha})`); body.addColorStop(1, `rgba(${r},${g2},${b},${0.15 * alpha})`);
        ctx.fillStyle = body;
        ctx.beginPath();
        ctx.moveTo(p.x - 22 * s, fy); ctx.quadraticCurveTo(p.x - 22 * s, fy - 34 * s + bob, p.x, fy - 38 * s + bob);
        ctx.quadraticCurveTo(p.x + 22 * s, fy - 34 * s + bob, p.x + 22 * s, fy); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.arc(p.x, fy - 52 * s + bob, 11 * s, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(0,0,0,.25)";
        for (let yy = fy - 64 * s; yy < fy; yy += 4 * s) ctx.fillRect(p.x - 23 * s, yy + ((t / 40) % (4 * s)), 46 * s, 1 * s);
        // name plate
        ctx.font = `600 ${12 * s}px Inter, system-ui, sans-serif`; ctx.textAlign = "center";
        ctx.fillStyle = `rgba(235,248,252,${alpha})`; ctx.fillText(p.a.name, p.x, p.y + 30 * s);
        ctx.font = `${9.5 * s}px ui-monospace, Consolas, monospace`;
        ctx.fillStyle = busy ? `rgba(${r},${g2},${b},1)` : `rgba(150,180,190,${alpha})`;
        ctx.fillText(busy ? "● AL LAVORO" : `oggi ${p.a.today}`, p.x, p.y + 44 * s);
        hit.push({ id: p.a.id, x: p.x / dpr, y: (p.y - 30 * s) / dpr, r: 48 * p.scale });
      }
      spots.current = hit;
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  const at = (e: React.PointerEvent) => {
    const r = (e.currentTarget as HTMLCanvasElement).getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    return spots.current.find((s) => Math.hypot(s.x - x, s.y - y) < s.r)?.id || null;
  };
  const sel = team?.agents.find((a) => a.id === pick) || null;

  return <div className="team">
    <header className="team-head">
      <span className="eyebrow">QUARTIER GENERALE</span>
      <h2>La squadra di {team?.leader.name || "Maria"}</h2>
      <p>{team ? `${team.agents.length} agenti · ${team.leader.today} azioni nelle ultime 24 ore` : "Raduno la squadra…"}</p>
    </header>
    <canvas ref={canvas} className="team-canvas"
      onPointerDown={(e) => { const id = at(e); if (id) setPick(id === pick ? null : id); else { view.current.drag = true; view.current.x = e.clientX; } }}
      onPointerMove={(e) => { const v = view.current; hover.current = at(e); (e.currentTarget as HTMLCanvasElement).style.cursor = hover.current ? "pointer" : v.drag ? "grabbing" : "grab";
        if (v.drag) { v.rot += (e.clientX - v.x) * 0.006; v.x = e.clientX; } }}
      onPointerUp={() => { view.current.drag = false; }} onPointerLeave={() => { view.current.drag = false; hover.current = null; }} />
    {sel && <aside className="team-card" style={{ ["--c" as string]: sel.color }}>
      <button className="close" aria-label="Chiudi" onClick={() => setPick(null)}>×</button>
      <small>{sel.business ? `${sel.business} / ` : ""}Squadra di {team?.leader.name}</small>
      <h3>{team?.leader.name} {sel.name}</h3>
      <div className="tags"><span>Agente AI</span><span className={sel.status === "al lavoro" ? "live" : ""}>{sel.status}</span></div>
      <label>IL DIPENDENTE CHE SOSTITUISCE</label><p>{sel.replaces}</p>
      <label>COSA FA</label><p>{sel.does}</p>
      <label>OGGI</label><p>{sel.today} azioni{sel.last ? ` · l'ultima: ${sel.last.what}, ${ago(sel.last.at)}` : ""}</p>
      {!!sel.facts.length && <ul>{sel.facts.map((f) => <li key={f}>{f}</li>)}</ul>}
    </aside>}
    {!sel && <p className="team-hint">Tocca un agente per vedere cosa fa · trascina per girare la stanza</p>}
  </div>;
}
