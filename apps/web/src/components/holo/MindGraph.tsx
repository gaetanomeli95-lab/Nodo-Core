import { useEffect, useRef, useState } from "react";
import { get } from "../../api";
import type { PresenceMode } from "../Presence";
import { orbitRadii } from "./mindMotion";

/** Maria's mind, live: the real memory (people, rules, facts, diary, customers, products, the board) as a 3D
 *  constellation that turns slowly around her. When a name is heard or said, its node lights up and the links
 *  to it pulse: Gaetano SEES her remembering. Clock, a running log and the counters frame it like a cockpit.
 *  Canvas 2D on purpose: a few hundred nodes run smoothly on a small laptop GPU. */

type GNode = { id: string; label: string; kind: string; weight: number; words: string[] };
type Graph = { nodes: GNode[]; edges: [string, string][]; stats: Record<string, number> };
type P = GNode & { x: number; y: number; z: number; hot: number; seen: number; deg: number };
type Level = () => { input: number; output: number };

const COLOR: Record<string, [number, number, number]> = {
  core: [255, 222, 170], hub: [150, 232, 255], person: [255, 186, 112], rule: [255, 116, 138], pref: [196, 168, 255],
  fact: [128, 196, 255], day: [168, 182, 206], customer: [110, 236, 196], product: [255, 216, 120], note: [255, 156, 204], topic: [150, 164, 255],
};
const MODE_SPIN: Record<string, number> = { rest: 0.05, listen: 0.07, think: 0.16, act: 0.2, speak: 0.09,
  approve: 0.05, reconnect: 0.03 };
const HUB_FOR: [RegExp, string[]][] = [
  [/registro|conti|sospes|consegn|listino|giornata|incass|vendit/i, ["hub:negozio", "hub:clienti", "hub:prodotti"]],
  [/memoria|ricord|cerco nella/i, ["hub:ricordi", "hub:persone", "hub:regole"]],
  [/lavagna/i, ["hub:lavagna"]], [/diario/i, ["hub:diario"]], [/conversazion|vecchie chat|chatgpt/i, ["hub:chat"]], [/promemoria/i, ["gaetano"]],
];
const DAYS = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
const MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
  "ottobre", "novembre", "dicembre"];

function rand(seed: number) { const x = Math.sin(seed * 9301 + 49297) * 233280; return x - Math.floor(x); }
function hash(s: string) { let h = 7; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h); }

/** Hubs on a sphere around Maria, every memory clustered around its hub, then a few spring passes on the links. */
function layout(g: Graph): { pts: P[]; idx: Map<string, number>; edges: [number, number][]; soft: [number, number][] } {
  const pts: P[] = g.nodes.map((n) => ({ ...n, x: 0, y: 0, z: 0, hot: -1e9, seen: -1e9, deg: 0 }));
  const idx = new Map(pts.map((p, i) => [p.id, i]));
  const edges = g.edges.map(([a, b]) => [idx.get(a), idx.get(b)] as [number | undefined, number | undefined])
    .filter((e): e is [number, number] => e[0] !== undefined && e[1] !== undefined);
  // a word lights a node only if it is distinctive: «materassi» is in half the memory, «Marcello» is one person
  const df = new Map<string, number>();
  for (const p of pts) for (const w of new Set(p.words)) df.set(w, (df.get(w) || 0) + 1);
  for (const p of pts) p.words = p.words.filter((w, k) => (k === 0 && w.includes(" ")) || (df.get(w) || 0) <= 3);
  const nbr: number[][] = pts.map(() => []);
  for (const [a, b] of edges) { nbr[a].push(b); nbr[b].push(a); pts[a].deg++; pts[b].deg++; }
  const hubs = pts.filter((p) => p.kind === "hub");
  hubs.forEach((h, i) => {
    const t = Math.acos(1 - 2 * (i + 0.5) / hubs.length), f = Math.PI * (1 + Math.sqrt(5)) * i;
    h.x = Math.sin(t) * Math.cos(f) * 0.62; h.y = Math.cos(t) * 0.5; h.z = Math.sin(t) * Math.sin(f) * 0.62;
  });
  pts.forEach((p, i) => {
    if (p.kind === "hub" || p.kind === "core") return;
    const home = nbr[i].map((j) => pts[j]).find((q) => q.kind === "hub" || q.id === "gaetano") || hubs[0];
    const s = hash(p.id), r = 0.12 + rand(s) * 0.3, th = rand(s + 1) * Math.PI * 2, ph = Math.acos(2 * rand(s + 2) - 1);
    const out = 1.2 + rand(s + 3) * 0.3;
    p.x = (home?.x ?? 0) * out + r * Math.sin(ph) * Math.cos(th);
    p.y = (home?.y ?? 0) * out + r * Math.cos(ph) * 0.8;
    p.z = (home?.z ?? 0) * out + r * Math.sin(ph) * Math.sin(th);
  });
  for (let it = 0; it < 24; it++) {                       // links pull related memories a little closer
    for (const [a, b] of edges) {
      const A = pts[a], B = pts[b];
      if (A.kind === "core" || B.kind === "core") continue;
      const dx = B.x - A.x, dy = B.y - A.y, dz = B.z - A.z, d = Math.hypot(dx, dy, dz) || 1, k = (d - 0.3) / d * 0.02;
      if (A.kind !== "hub") { A.x += dx * k; A.y += dy * k; A.z += dz * k; }
      if (B.kind !== "hub") { B.x -= dx * k; B.y -= dy * k; B.z -= dz * k; }
    }
  }
  // memories of the same kind sit close and touch: a light mesh between neighbours makes the cluster read as one mind
  const soft: [number, number][] = [];
  const home = pts.map((_, i) => nbr[i].find((j) => pts[j].kind === "hub") ?? -1);
  pts.forEach((p, i) => {
    if (home[i] < 0 || p.kind === "hub") return;
    const near = pts.map((q, j) => [j, home[j] === home[i] && j !== i ? Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z) : 9] as const)
      .sort((a, b) => a[1] - b[1]).slice(0, 3).filter(([, d]) => d < 0.5);
    for (const [j] of near) if (i < j) soft.push([i, j]);
  });
  return { pts, idx, edges, soft };
}

function norm(t: string) { return ` ${t.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9' ]+/g, " ")} `; }

export function MindGraph({ mode, sampleLevel, heard, said, activity, revision }: {
  mode: PresenceMode; sampleLevel: Level; heard?: string; said?: string; activity?: string | null; revision?: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const graph = useRef<(ReturnType<typeof layout> & { born?: number }) | null>(null);
  const live = useRef({ mode, sampleLevel });
  live.current = { mode, sampleLevel };
  const [stats, setStats] = useState<Record<string, number>>({});
  const [log, setLog] = useState<{ t: string; text: string; hot?: boolean }[]>([]);
  const [now, setNow] = useState(() => new Date());
  const drag = useRef({ on: false, x: 0, y: 0, rot: 0, tilt: 0.32, px: 0, py: 0, sx: 0, sy: 0 });

  const note = (text: string, hot = false) => setLog((l) => [{ t: new Date().toTimeString().slice(0, 8), text, hot },
    ...l.filter((x) => x.text !== text)].slice(0, 6));

  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);

  useEffect(() => {                                     // the graph: at start, then when memory may have changed
    let alive = true;
    get<Graph>("/api/v1/mind/graph").then((g) => {
      if (!alive || !g?.nodes?.length) return;
      const old = graph.current;
      const next = Object.assign(layout(g), { born: old ? (old as { born?: number }).born ?? 0 : performance.now() });
      if (old) next.pts.forEach((p) => { const o = old.idx.get(p.id); if (o !== undefined) { p.hot = old.pts[o].hot; p.seen = old.pts[o].seen; } });
      graph.current = next;
      setStats(g.stats || {});
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [revision]);

  const touch = (text: string | undefined, label: string) => {   // light up what was just heard or said
    const g = graph.current;
    if (!g || !text) return;
    const n = norm(text), t = performance.now();
    const rank: Record<string, number> = { person: 0, customer: 1, rule: 2, note: 3, product: 4 };
    const found = g.pts.filter((p) => p.kind !== "core" && p.kind !== "hub"
      && p.words.some((w) => w.length >= 4 && n.includes(` ${norm(w).trim()}`)))
      .sort((a, b) => (rank[a.kind] ?? 9) - (rank[b.kind] ?? 9));
    const lit: string[] = [];
    found.forEach((p, k) => {
      if (k < 4) { if (t - p.hot > 4000) lit.push(p.label); p.hot = t; }   // the few that matter get a red label
      p.seen = t;
      for (const [a, b] of g.edges) {                    // its neighbours glow softly
        if (g.pts[a] === p) g.pts[b].seen = t; else if (g.pts[b] === p) g.pts[a].seen = t;
      }
    });
    if (lit.length) note(`${label} · ${lit.slice(0, 3).join(", ")}`, true);
  };
  useEffect(() => touch(heard, "sento"), [heard]);     // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => touch(said, "ricordo"), [said]);     // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!activity) return;
    note(activity);
    const g = graph.current;
    if (!g) return;
    const t = performance.now();
    for (const [rx, ids] of HUB_FOR) if (rx.test(activity)) for (const id of ids) {
      const i = g.idx.get(id); if (i !== undefined) { g.pts[i].hot = t; g.pts[i].seen = t; }
    }
  }, [activity]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    let raf = 0, last = performance.now(), energy = 0;
    const sparks: { e: number; u: number; v: number; soft: boolean }[] = [];   // thoughts running along the links
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const fit = () => { const r = c.getBoundingClientRect(); c.width = Math.max(1, r.width * dpr); c.height = Math.max(1, r.height * dpr); };
    fit();
    const ro = new ResizeObserver(fit); ro.observe(c);

    const draw = (t: number) => {
      raf = requestAnimationFrame(draw);
      if (document.hidden) { last = t; return; }
      const dt = Math.min(0.1, (t - last) / 1000);
      if (dt < 1 / 40) return;                           // ~30 fps: plenty, and gentle on a small PC
      last = t;
      const g = graph.current, { mode: m, sampleLevel: lvl } = live.current;
      const lv = lvl(); energy += (Math.max(lv.input, lv.output) - energy) * 0.3;
      const dr = drag.current;
      if (!dr.on && !reduced) dr.rot += dt * (MODE_SPIN[m] ?? 0.05);
      // the mind lives on the screen, not on the page: it never slides away when cards appear while she works
      dr.sx += (dr.px - dr.sx) * 0.05; dr.sy += (dr.py - dr.sy) * 0.05;          // follows the hand, gently
      const W = c.width, H = c.height, cx = W / 2, cy = H * 0.56, S = Math.min(W * 0.3, H * 0.34);
      const rot = dr.rot + dr.sx * 0.5, tilt = Math.max(-0.2, Math.min(1.1, dr.tilt + dr.sy * 0.25));
      ctx.clearRect(0, 0, W, H);
      const cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(tilt), st = Math.sin(tilt);
      const orbit = orbitRadii(S, st);
      const proj = (x: number, y: number, z: number) => {
        const x1 = x * cr + z * sr, z1 = -x * sr + z * cr;
        const y2 = y * ct - z1 * st, z2 = y * st + z1 * ct;
        const f = 3.2 / (3.2 + z2);
        return [cx + x1 * f * S, cy + y2 * f * S, f, z2] as const;
      };
      // the orbit: a dashed ring with a few lights running on it
      ctx.save();
      ctx.strokeStyle = "rgba(160,220,235,.22)"; ctx.lineWidth = 1 * dpr; ctx.setLineDash([2 * dpr, 7 * dpr]);
      ctx.beginPath(); ctx.ellipse(cx, cy + S * 0.05, orbit.x, orbit.y, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      for (let k = 0; k < 3; k++) {
        const a = rot * 2 + k * 2.1, ex = cx + Math.cos(a) * orbit.x, ey = cy + S * 0.05 + Math.sin(a) * orbit.y;
        ctx.fillStyle = "rgba(190,240,255,.8)"; ctx.beginPath(); ctx.arc(ex, ey, 1.8 * dpr, 0, Math.PI * 2); ctx.fill();
      }
      // thinking: waves of light leave her core and run across the orbit; speaking: they beat with the voice
      if (m === "think" || m === "act" || m === "speak" || m === "listen") {
        const n = m === "speak" || m === "listen" ? 2 : 3, period = m === "speak" || m === "listen" ? 1700 : 1150;
        for (let k = 0; k < n; k++) {
          const u = ((t / period) + k / n) % 1, rx = S * (0.15 + u * 1.55), ry = rx * (st * 0.9) + S * 0.05 * u;
          ctx.strokeStyle = m === "think" || m === "act" ? `rgba(190,160,255,${0.32 * (1 - u)})`
            : `rgba(150,235,255,${(0.18 + energy * 0.6) * (1 - u)})`;
          ctx.lineWidth = (1 + (1 - u) * 1.5) * dpr;
          ctx.beginPath(); ctx.ellipse(cx, cy + S * 0.05, rx, Math.max(1, ry), 0, 0, Math.PI * 2); ctx.stroke();
        }
      }
      ctx.restore();
      if (!g) return;
      if ((m === "think" || m === "act") && !reduced) {    // she is searching: her attention sweeps the hubs
        const hubs = g.pts.filter((p) => p.kind === "hub");
        if (hubs.length) hubs[Math.floor(t / 380) % hubs.length].seen = t - 4200;
      }
      const born = g.born ?? 0;
      const grow = (i: number) => {                       // at start the memories fly out of her core and settle
        const k = Math.min(1, Math.max(0, (t - born - (hash(g.pts[i].id) % 900)) / 1500));
        return 1 - Math.pow(1 - k, 3);
      };
      const br = reduced ? 0 : 0.012;
      const pr = g.pts.map((p, i) => { const k = grow(i);
        return proj((p.x + Math.sin(t / 1700 + i) * br) * k, (p.y + Math.cos(t / 2100 + i * 1.3) * br) * k, p.z * k); });
      // links: faint by default, bright and pulsing near what Maria is remembering now (additive light)
      ctx.globalCompositeOperation = "lighter";
      ctx.lineWidth = 0.7 * dpr;
      ctx.strokeStyle = `rgba(150,205,240,${0.09 + energy * 0.12})`;
      ctx.beginPath();
      const hotEdges: [number, number, number][] = [];
      for (const [a, b] of g.edges) {
        const ha = t - g.pts[a].hot, hb = t - g.pts[b].hot, heat = Math.max(0, 1 - Math.min(ha, hb) / 7000);
        if (heat > 0) { hotEdges.push([a, b, heat]); continue; }
        ctx.moveTo(pr[a][0], pr[a][1]); ctx.lineTo(pr[b][0], pr[b][1]);
      }
      ctx.stroke();
      ctx.strokeStyle = `rgba(120,190,230,${0.08 + energy * 0.1})`;
      ctx.beginPath();
      for (const [a, b] of g.soft) { ctx.moveTo(pr[a][0], pr[a][1]); ctx.lineTo(pr[b][0], pr[b][1]); }
      ctx.stroke();
      for (const [a, b, heat] of hotEdges) {
        ctx.strokeStyle = `rgba(255,150,120,${0.25 + heat * 0.55})`; ctx.lineWidth = (0.8 + heat * 1.4) * dpr;
        ctx.beginPath(); ctx.moveTo(pr[a][0], pr[a][1]); ctx.lineTo(pr[b][0], pr[b][1]); ctx.stroke();
        const u = ((t / 900) + a * 0.13) % 1;              // a spark travels along the link
        ctx.fillStyle = `rgba(255,230,200,${heat})`;
        ctx.beginPath(); ctx.arc(pr[a][0] + (pr[b][0] - pr[a][0]) * u, pr[a][1] + (pr[b][1] - pr[a][1]) * u, 1.8 * dpr, 0, Math.PI * 2); ctx.fill();
      }
      // thoughts: sparks running along the links, many more while she thinks, works or speaks
      const busy = m === "think" || m === "act" ? 26 : m === "speak" || m === "listen" ? 14 : 6;
      const nE = g.edges.length, nS = g.soft.length;
      while (sparks.length < busy && nE + nS > 0) {
        const soft = nS > 0 && Math.random() < 0.35;
        sparks.push({ e: Math.floor(Math.random() * (soft ? nS : nE)), u: 0, v: 0.35 + Math.random() * 0.6, soft });
      }
      if (sparks.length > busy) sparks.length = busy;
      for (const sp of sparks) {
        const [a, b] = (sp.soft ? g.soft : g.edges)[sp.e] || [0, 0];
        sp.u += dt * sp.v * (m === "think" || m === "act" ? 1.8 : 1);
        if (sp.u >= 1) { sp.u = 0; const soft = nS > 0 && Math.random() < 0.35; sp.soft = soft;
          sp.e = Math.floor(Math.random() * (soft ? nS : nE)); continue; }
        const x = pr[a][0] + (pr[b][0] - pr[a][0]) * sp.u, y = pr[a][1] + (pr[b][1] - pr[a][1]) * sp.u;
        const tx = pr[a][0] + (pr[b][0] - pr[a][0]) * Math.max(0, sp.u - 0.12), ty = pr[a][1] + (pr[b][1] - pr[a][1]) * Math.max(0, sp.u - 0.12);
        const fade = Math.sin(sp.u * Math.PI);
        ctx.strokeStyle = `rgba(170,235,255,${0.5 * fade})`; ctx.lineWidth = 1.4 * dpr;
        ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = `rgba(230,250,255,${0.9 * fade})`; ctx.beginPath(); ctx.arc(x, y, 1.6 * dpr, 0, Math.PI * 2); ctx.fill();
      }
      // nodes, back to front
      const order = pr.map((_, i) => i).sort((i, j) => pr[j][3] - pr[i][3]);
      const labels: { i: number; hot: number }[] = [];
      for (const i of order) {
        const p = g.pts[i], [x, y, f, z] = pr[i], c0 = COLOR[p.kind] || COLOR.fact;
        const hot = Math.max(0, 1 - (t - p.hot) / 7000), seen = Math.max(0, 1 - (t - p.seen) / 7000);
        const depth = Math.max(0.25, Math.min(1, 0.75 - z * 0.35));
        let r = (1.5 + Math.sqrt(p.weight) * 1.2) * f * dpr;
        if (p.kind === "hub") r *= 1.5;
        if (p.kind === "core") r = (7 + energy * 16 + Math.sin(t / 500) * 1.2) * dpr;
        r *= 1 + hot * 1.4 + seen * 0.4;
        if (p.kind === "core" || hot > 0 || p.kind === "hub") {
          const gl = ctx.createRadialGradient(x, y, 0, x, y, r * (p.kind === "core" ? 5 : 4));
          const col = hot > 0 ? [255, 140, 110] : c0;
          gl.addColorStop(0, `rgba(${col[0]},${col[1]},${col[2]},${p.kind === "core" ? 0.55 : 0.18 + hot * 0.45})`);
          gl.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = gl; ctx.beginPath(); ctx.arc(x, y, r * (p.kind === "core" ? 5 : 4), 0, Math.PI * 2); ctx.fill();
        }
        ctx.fillStyle = hot > 0 ? `rgba(255,${170 - hot * 60},${140 - hot * 60},1)`
          : `rgba(${c0[0]},${c0[1]},${c0[2]},${depth * (0.55 + seen * 0.45)})`;
        ctx.beginPath(); ctx.arc(x, y, Math.max(0.8 * dpr, r), 0, Math.PI * 2); ctx.fill();
        if (p.kind === "hub" || hot > 0 || (p.kind === "person" && z < -0.15) || (p.kind !== "topic" && seen > 0.6)) labels.push({ i, hot });
      }
      ctx.globalCompositeOperation = "source-over";
      // labels as small glass pills; what she is remembering NOW in red
      ctx.font = `${10.5 * dpr}px Inter, system-ui, sans-serif`; ctx.textBaseline = "middle";
      const pick = [...labels.filter((l) => l.hot > 0), ...labels.filter((l) => l.hot === 0 && g.pts[l.i].kind === "hub"),
        ...labels.filter((l) => l.hot === 0 && g.pts[l.i].kind !== "hub")].slice(0, 26);
      for (const { i, hot } of pick) {
        const p = g.pts[i], [x, y] = pr[i], text = p.label, w = ctx.measureText(text).width + 12 * dpr, h = 17 * dpr;
        const lx = x + 9 * dpr, ly = y - h / 2;
        ctx.fillStyle = hot > 0 ? `rgba(214,60,82,${0.55 + hot * 0.35})` : p.kind === "hub" ? "rgba(20,48,60,.72)" : "rgba(16,30,40,.62)";
        ctx.beginPath(); ctx.roundRect(lx, ly, w, h, 4 * dpr); ctx.fill();
        ctx.fillStyle = hot > 0 ? "#fff" : p.kind === "hub" ? "rgba(190,240,255,.95)" : "rgba(220,230,235,.85)";
        ctx.fillText(text, lx + 6 * dpr, y + 0.5 * dpr);
      }
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  useEffect(() => {                                   // drag only inside the mind's visible area
    const free = (el: EventTarget | null) => el instanceof Element
      && !!el.closest(".mind-spacer")
      && !el.closest("button, a, input, textarea, select, label, [role=button], .board, .context-sheet, .viewer, .palette, nav, header, aside");
    const down = (e: PointerEvent) => { if (e.button === 0 && free(e.target)) { const d = drag.current; d.on = true; d.x = e.clientX; d.y = e.clientY; } };
    const move = (e: PointerEvent) => {
      const d = drag.current;
      if (free(e.target) || d.on) {
        d.px = e.clientX / window.innerWidth - 0.5; d.py = e.clientY / window.innerHeight - 0.5;
      }
      if (!d.on) return;
      d.rot += (e.clientX - d.x) * 0.006; d.tilt = Math.max(-0.2, Math.min(1.1, d.tilt + (e.clientY - d.y) * 0.004));
      d.x = e.clientX; d.y = e.clientY;
    };
    const up = () => { drag.current.on = false; };
    window.addEventListener("pointerdown", down); window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up); window.addEventListener("blur", up);
    return () => { window.removeEventListener("pointerdown", down); window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); window.removeEventListener("blur", up); };
  }, []);

  return <div className="mind" aria-hidden="true">
    <canvas ref={canvas} className="mind-canvas" />
    <div className="mind-clock"><b>{now.toTimeString().slice(0, 8)}</b>
      <span>{DAYS[now.getDay()]} {now.getDate()} {MONTHS[now.getMonth()]}</span>
      {!!stats.nodi && <div className="mind-stats">
        <div><b>{stats.ricordi ?? 0}</b><span>ricordi</span></div>
        <div><b>{stats.clienti ?? 0}</b><span>clienti</span></div>
        <div><b>{(stats.collegamenti ?? 0).toLocaleString("it-IT")}</b><span>collegamenti</span></div>
        <div><b>{(stats.conversazioni ?? 0).toLocaleString("it-IT")}</b><span>conversazioni</span></div>
      </div>}</div>
    {!!log.length && <ul className="mind-log">{log.map((l) => <li key={l.t + l.text} className={l.hot ? "hot" : ""}><i>{l.t}</i>{l.text}</li>)}</ul>}
  </div>;
}
