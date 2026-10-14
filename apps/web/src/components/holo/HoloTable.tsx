import { useEffect, useMemo, useRef, useState } from "react";
import { calm, lite } from "../../motion";

/** «Sul tavolo», as a hologram. What Maria puts in front of you (the register, who still has to pay, the sites,
 *  a file) rises from a projector as a ring of glass tiles floating in 3D: drag it, turn it with the wheel or
 *  the arrows, click a tile to bring it to the front. The numbers count up, colours tell what each row is
 *  (green incasso, rose sospeso, amber da fare, violet uscita). Pure CSS 3D transforms: the graphics chip
 *  composes them almost for free, so it stays smooth on the shop laptop too. */

export type HoloSurface = { view: string; rows: Record<string, unknown>[]; summary: string; totals?: Record<string, number> };

const TONE: Record<string, string> = {
  sospeso: "rose", da_fare: "amber", vendita: "mint", credito: "rose", spesa: "violet", sostituzione: "cyan",
  ok: "mint", warning: "amber", critical: "rose", open: "amber", in_progress: "cyan", done: "mint",
};
const LABEL: Record<string, string> = {
  sospeso: "Sospeso", da_fare: "Da fare", vendita: "Vendita", credito: "Credito", spesa: "Uscita",
  sostituzione: "Sostituzione", ok: "Regolare", warning: "Da controllare", critical: "Attenzione", unknown: "Da verificare",
  open: "Da fare", in_progress: "In corso", blocked: "In attesa", active: "Attivo", done: "Fatto",
};

function useCount(target: number, ms = 1100) {
  const [v, setV] = useState(calm ? target : 0);
  useEffect(() => {
    if (calm) { setV(target); return; }
    let raf = 0; const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms), e = 1 - (1 - k) ** 3;
      setV(target * e);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

function Kpi({ label, value, tone }: { label: string; value: number; tone: string }) {
  const v = useCount(value);
  return <div className={`holo-kpi ${tone}`}><b>{v.toLocaleString("it-IT", { maximumFractionDigits: 0 })}<small> €</small></b>
    <span>{label}</span></div>;
}

/** «Incassato 140 € · uscite 0 € · saldo 140 € · sospesi 430 €» -> numbers to show big. */
function kpis(s: HoloSurface): [string, number, string][] {
  const t = s.totals;
  if (t && Object.keys(t).length) {
    const out: [string, number, string][] = [["Incassato", t.incassato || 0, "mint"], ["Uscite", t.uscite || 0, "violet"],
      ["Saldo", t.saldo_del_periodo || 0, "cyan"]];
    if (t.da_incassare_sospeso) out.push(["Sospesi", t.da_incassare_sospeso, "rose"]);
    return out;
  }
  const out: [string, number, string][] = [];
  for (const m of s.summary.matchAll(/([A-Za-zÀ-ù ]{3,20}?)[:\s]+(-?\d+(?:[.,]\d+)?)\s*€/g)) {
    const name = m[1].trim(), n = Number(m[2].replace(",", "."));
    out.push([name[0].toUpperCase() + name.slice(1), n, /sospes|incassare/i.test(name) ? "rose" : /usc/i.test(name) ? "violet" : "mint"]);
  }
  return out.slice(0, 4);
}

export function HoloTable({ surface, title, onClose, onDownload }: {
  surface: HoloSurface; title: string; onClose: () => void; onDownload: (url: string) => void;
}) {
  const rows = surface.rows;
  const n = rows.length;
  const ring = useRef<HTMLDivElement>(null);
  const tiles = useRef<(HTMLDivElement | null)[]>([]);
  const [front, setFront] = useState(0);
  const st = useRef({ rot: 0, vel: 0, target: 0 as number | null, drag: false, x: 0, moved: 0 });
  const step = n > 1 ? 30 : 0;                     // degrees between tiles on the arc
  const radius = n > 1 ? 560 : 0;
  const k = useMemo(() => kpis(surface), [surface]);

  const maxRot = Math.max(0, (n - 1) * step);
  useEffect(() => {
    tiles.current = tiles.current.slice(0, n);
    let raf = 0, last = performance.now(), shown = -1;
    const s = st.current;
    s.rot = 0; s.vel = 0; s.target = 0;
    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.05, (t - last) / 1000); last = t;
      if (!s.drag && s.target !== null) {               // glide to the chosen tile
        const d = s.target - s.rot; s.rot += d * Math.min(1, dt * 7);
        if (Math.abs(d) < 0.02) s.rot = s.target;
      }
      const bob = calm ? 0 : Math.sin(t / 1400) * 4;    // the hologram floats
      if (ring.current) ring.current.style.transform =
        `translateY(${bob}px) translateZ(${-radius}px) rotateY(${-s.rot}deg)`;
      let best = 0, bestD = 999;
      for (let i = 0; i < n; i++) {
        const el = tiles.current[i]; if (!el) continue;
        const d = n === 1 ? 0 : i * step - s.rot, a = Math.abs(d);
        if (a < bestD) { bestD = a; best = i; }
        el.style.opacity = String(a > 100 ? 0 : Math.max(0.1, 1 - a / 90));
        el.style.pointerEvents = a > 70 ? "none" : "auto";
        el.classList.toggle("front", a < step / 2 + 0.01);
      }
      if (best !== shown) { shown = best; setFront(best); }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [n, step, radius]);

  const goTo = (i: number) => { const s = st.current; s.target = Math.max(0, Math.min(n - 1, i)) * step; };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") goTo(front + 1);
      if (e.key === "ArrowLeft") goTo(front - 1);
    };
    addEventListener("keydown", key); return () => removeEventListener("keydown", key);
  });  // eslint-disable-line react-hooks/exhaustive-deps

  const down = (e: React.PointerEvent) => {
    if (e.button !== 0 || n < 2) return;
    const s = st.current; s.drag = true; s.x = e.clientX; s.moved = 0; s.target = null; s.vel = 0;
    let lastT = performance.now();
    // no pointer capture: it would steal the click from the tiles and their buttons
    const drag = (ev: PointerEvent) => {
      const now = performance.now(), dx = ev.clientX - s.x; s.x = ev.clientX; s.moved += Math.abs(dx);
      const out = s.rot < 0 || s.rot > maxRot;           // past the ends: it stretches, like rubber
      s.rot -= dx * 0.22 * (out ? 0.35 : 1);
      s.vel = (-dx * 0.22) / Math.max(1, now - lastT) * 1000; lastT = now;
    };
    const stop = () => {
      removeEventListener("pointermove", drag); removeEventListener("pointerup", stop); removeEventListener("pointercancel", stop);
      if (!s.drag) return;
      s.drag = false;
      const fling = Math.max(-2, Math.min(2, Math.round((s.vel * 0.18) / step)));   // a flick moves up to 2 tiles
      goTo(Math.round(s.rot / step) + fling);
    };
    addEventListener("pointermove", drag); addEventListener("pointerup", stop); addEventListener("pointercancel", stop);
  };
  const move = (e: React.PointerEvent) => {
    const stage = e.currentTarget as HTMLElement, r = stage.getBoundingClientRect();
    stage.style.setProperty("--mx", String((e.clientX - r.left) / r.width));      // the sheen follows the hand
    stage.style.setProperty("--my", String((e.clientY - r.top) / r.height));
  };
  const wheelAt = useRef(0);
  const wheel = (e: React.WheelEvent) => {
    const now = performance.now(), d = e.deltaY + e.deltaX;
    if (Math.abs(d) < 4 || now - wheelAt.current < 220) return;
    wheelAt.current = now; goTo(front + (d > 0 ? 1 : -1));
  };

  const sel = rows[front] || {};
  return <section className={`holo-table${lite ? " lite" : ""}`} aria-label="Sul tavolo" role="region">
    <div className="holo-table-head">
      <span className="sheet-kicker">SUL TAVOLO · OLOGRAMMA</span>
      <h3>{title}</h3>
      <button aria-label="Riponi" className="holo-x" onClick={onClose}>×</button>
    </div>
    {k.length > 0 && <div className="holo-kpis">{k.map(([l, v, tone]) => <Kpi key={l} label={l} value={v} tone={tone} />)}</div>}
    {!k.length && surface.summary && <p className="holo-summary">{surface.summary}</p>}
    {n > 1 && <div className="holo-nav">
      <button aria-label="Precedente" disabled={front === 0} onClick={() => goTo(front - 1)}>‹</button>
      <span>{front + 1} / {n}{sel.title ? ` — ${String(sel.title).slice(0, 60)}` : ""}</span>
      <button aria-label="Successivo" disabled={front === n - 1} onClick={() => goTo(front + 1)}>›</button>
    </div>}
    {n > 0 && <div className="holo-stage" onPointerDown={down} onPointerMove={move} onWheel={wheel}>
      <div className="holo-beam" />
      <div className="holo-ring" ref={ring}>
        {rows.map((row, i) => {
          const status = String(row.status || row.kind || "");
          const tone = TONE[status] || (row.download ? "cyan" : "cyan");
          const amount = row.amount != null ? Number(row.amount) : null;
          return <div key={i} ref={(el) => { tiles.current[i] = el; }} className={`holo-tile ${tone}`}
            style={{ transform: `rotateY(${i * step}deg) translateZ(${radius}px)`, animationDelay: `${Math.min(i, 12) * 70}ms` }}
            onClick={() => { if (st.current.moved < 6) goTo(i); }}>
            <i className="holo-tile-edge" />
            <span className="holo-tile-tag">{LABEL[status] || (row.download ? "File" : "")}{row.day ? ` · ${String(row.day).slice(8, 10)}/${String(row.day).slice(5, 7)}` : ""}</span>
            <strong>{String(row.title || row.name || "")}</strong>
            {amount != null && <em>{amount.toLocaleString("it-IT")} €</em>}
            {row.download && row.url ? <button className="primary" onClick={(e) => { e.stopPropagation(); onDownload(String(row.url)); }}>Scarica ↓</button>
              : row.url ? <a href={String(row.url)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Apri ↗</a> : null}
          </div>;
        })}
      </div>
      <div className="holo-projector"><i /><i /><i /></div>
    </div>}
    {!n && !surface.summary && <p className="holo-summary">Non ci sono elementi da mostrare.</p>}
  </section>;
}
