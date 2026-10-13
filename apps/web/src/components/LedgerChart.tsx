import { useMemo, useState } from "react";
import type { LedgerStats } from "../api";

const eur = (n: number) => n.toLocaleString("it-IT", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const short = (d: string) => { const x = new Date(`${d}T12:00:00`); return `${x.getDate()}/${x.getMonth() + 1}`; };

/** Money in (blue) and out (orange) per day: paired thin bars on one axis, hover for the exact numbers. */
export function MoneyChart({ stats }: { stats: LedgerStats }) {
  const [hover, setHover] = useState<number | null>(null);
  const s = stats.series;
  const W = 760, H = 210, padL = 44, padB = 22, padT = 10;
  const max = useMemo(() => {
    const m = Math.max(10, ...s.map((d) => Math.max(d.in, d.out)));
    const step = 10 ** Math.floor(Math.log10(m));
    return Math.ceil(m / step) * step;
  }, [s]);
  const slot = (W - padL) / s.length;
  const every = Math.ceil(s.length / 8);
  const bw = Math.max(2, Math.min(10, slot / 2 - 2));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const ticks = [0, max / 2, max];
  const h = hover !== null ? s[hover] : null;
  return <figure className="money-chart">
    <figcaption>
      <span>Entrate e uscite, ultimi {stats.days} giorni</span>
      <span className="legend"><i className="in" />Incassato <i className="out" />Uscite</span>
    </figcaption>
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Incassato ${eur(stats.totals.incassato)}, uscite ${eur(stats.totals.uscite)}`}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => <g key={t}><line x1={padL} x2={W} y1={y(t)} y2={y(t)} className="grid" />
          <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="tick">{eur(t)}</text></g>)}
        {s.map((d, i) => {
          const x0 = padL + i * slot + slot / 2;
          return <g key={d.day} onMouseEnter={() => setHover(i)}>
            <rect x={padL + i * slot} y={padT} width={slot} height={H - padT - padB} className={`hit ${hover === i ? "on" : ""}`} />
            {d.in > 0 && <path className="bar in" d={bar(x0 - bw - 1, y(d.in), bw, y(0) - y(d.in))} />}
            {d.out > 0 && <path className="bar out" d={bar(x0 + 1, y(d.out), bw, y(0) - y(d.out))} />}
            {(i % every === 0 || (i === s.length - 1 && i % every >= every / 2)) &&
              <text x={x0} y={H - 6} textAnchor="middle" className="tick">{short(d.day)}</text>}
          </g>;
        })}
        <line x1={padL} x2={W} y1={y(0)} y2={y(0)} className="axis" />
      </svg>
      {h && <div className="chart-tip" style={{ left: `${((padL + (hover! + 0.5) * slot) / W) * 100}%` }}>
        <b>{new Date(`${h.day}T12:00:00`).toLocaleDateString("it-IT", { weekday: "short", day: "numeric", month: "short" })}</b>
        <span><i className="in" />Incassato {eur(h.in)}</span><span><i className="out" />Uscite {eur(h.out)}</span>
        <span>{h.pieces} {h.pieces === 1 ? "pezzo" : "pezzi"} venduti</span>
      </div>}
    </div>
  </figure>;
}

/** A bar with 4px rounded top, square at the baseline. */
function bar(x: number, top: number, w: number, hgt: number) {
  const r = Math.min(4, w / 2, hgt);
  return `M${x},${top + hgt}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${top + hgt}Z`;
}
