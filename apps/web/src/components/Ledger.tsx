import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { downloadFile, get, type LedgerOpen, type LedgerReport, type LedgerRow, type NodoFile } from "../api";

const KIND: Record<string, [string, string]> = {
  vendita: ["Vendite", "sale"], sostituzione: ["Sostituzioni in garanzia", "swap"], spesa: ["Uscite", "out"], nota: ["Note", "note"],
};
const fmt = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("it-IT", { style: "currency", currency: "EUR", minimumFractionDigits: n % 1 ? 2 : 0 });
const iso = (d: Date) => d.toLocaleDateString("sv-SE");
const shift = (day: string, n: number) => { const d = new Date(`${day}T12:00:00`); d.setDate(d.getDate() + n); return iso(d); };
const sayDay = (day: string, today: string) => {
  const rel: Record<string, string> = { [today]: "Oggi", [shift(today, -1)]: "Ieri", [shift(today, 1)]: "Domani" };
  const d = new Date(`${day}T12:00:00`).toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" });
  return rel[day] ? `${rel[day]} · ${d}` : d.charAt(0).toUpperCase() + d.slice(1);
};

/** A number that glides to its new value: the totals feel alive when a row changes. */
function Counter({ value }: { value: number }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = performance.now(), a = from.current, b = value;
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - start) / 600), e = 1 - Math.pow(1 - k, 3);
      setShown(a + (b - a) * e);
      if (k < 1) raf = requestAnimationFrame(step); else from.current = b;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{fmt(Math.round(shown * 100) / 100)}</>;
}

function Row({ r }: { r: LedgerRow }) {
  return <li className={`ledger-row ${r.kind} ${r.status} ${r.payment}`}>
    <div className="what">
      <b>{r.qty > 1 ? `${r.qty} × ` : ""}{r.item || "—"}</b>
      {r.size && <span className="size">{r.size}</span>}
      {r.counterpart && <span className="who">{r.kind === "spesa" ? "a " : ""}{r.counterpart}</span>}
      {r.note && <small className="note">{r.note}</small>}
    </div>
    <div className="money">
      {r.kind === "sostituzione" ? <small>garanzia</small> : <strong>{fmt(r.amount)}</strong>}
      {r.qty > 1 && r.unit_price != null && <small>{fmt(r.unit_price)} l'uno</small>}
      {r.payment === "sospeso" && <span className="chip warn">sospeso</span>}
      {r.status === "da_fare" && <span className="chip">da fare</span>}
    </div>
  </li>;
}

export function LedgerView() {
  const [day, setDay] = useState<string | null>(null);
  const [span, setSpan] = useState<1 | 7>(1);
  const [rep, setRep] = useState<LedgerReport | null>(null);
  const [open, setOpen] = useState<LedgerOpen | null>(null);
  const [files, setFiles] = useState<NodoFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    const path = day ? `/api/v1/ledger?start=${span === 7 ? shift(day, -6) : day}&end=${day}` : "/api/v1/ledger";
    get<LedgerReport>(path).then((r) => {
      setRep(r); setError(null);
      if (!day) setDay(r.today);              // first load: learn «today» from NODO (its time zone, not the browser's)
    }).catch((e) => setError((e as Error).message));
    get<LedgerOpen>("/api/v1/ledger/open").then(setOpen).catch(() => {});
    get<{ files: NodoFile[] }>("/api/v1/files").then((f) => setFiles(f.files.slice(0, 12))).catch(() => {});
  }, [day, span]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 20_000); return () => clearInterval(t); }, [load]);

  const groups = useMemo(() => {
    const by: Record<string, Record<string, LedgerRow[]>> = {};
    for (const r of rep?.entries || []) ((by[r.day] ||= {})[r.kind] ||= []).push(r);
    return Object.entries(by).sort(([a], [b]) => b.localeCompare(a));
  }, [rep]);
  const today = rep?.today || day || iso(new Date());
  const t = rep?.totals;
  const copy = async () => { try { await navigator.clipboard.writeText(rep?.text || ""); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* no clipboard */ } };

  return <div className="view ledger">
    <header className="ledger-head">
      <div><span className="eyebrow">REGISTRO</span><h2 className="ledger-title">{rep?.business || "Negozio"}</h2></div>
      <div className="ledger-nav">
        <button className="ghost" aria-label="Giorno prima" onClick={() => day && setDay(shift(day, -1))}>‹</button>
        <button className="ghost day" onClick={() => setDay(today)}>{day ? (span === 7 ? `Settimana fino a ${sayDay(day, today).toLowerCase()}` : sayDay(day, today)) : "…"}</button>
        <button className="ghost" aria-label="Giorno dopo" disabled={!day || day >= today} onClick={() => day && setDay(shift(day, 1))}>›</button>
        <div className="seg"><button className={span === 1 ? "on" : ""} onClick={() => setSpan(1)}>Giorno</button>
          <button className={span === 7 ? "on" : ""} onClick={() => setSpan(7)}>7 giorni</button></div>
      </div>
    </header>
    {error && <div className="error">{error}</div>}

    <section className="kpis">
      <div className="kpi in"><small>Incassato</small><strong><Counter value={t?.incassato || 0} /></strong></div>
      <div className="kpi out"><small>Uscite</small><strong><Counter value={t?.uscite || 0} /></strong></div>
      <div className={`kpi bal ${(t?.saldo_del_periodo || 0) < 0 ? "neg" : ""}`}><small>Saldo</small><strong><Counter value={t?.saldo_del_periodo || 0} /></strong></div>
      <div className="kpi wait"><small>Da incassare</small><strong><Counter value={t?.da_incassare_sospeso || 0} /></strong></div>
    </section>

    <div className="ledger-grid">
      <section className="card ledger-days">
        {!groups.length && <p className="muted empty">Niente nel registro {span === 1 ? "per questo giorno" : "in questi giorni"}. Racconta a NODO cosa hai venduto, consegnato o speso: lo segna qui.</p>}
        {groups.map(([d, kinds]) => <div key={d} className="ledger-day">
          {span === 7 && <h4>{sayDay(d, today)}</h4>}
          {Object.entries(KIND).filter(([k]) => kinds[k]).map(([k, [label, cls]]) => <div key={k} className={`ledger-group ${cls}`}>
            <h5>{label}<span>{kinds[k].length}</span></h5>
            <ul>{kinds[k].map((r) => <Row key={r.id} r={r} />)}</ul>
          </div>)}
        </div>)}
        {!!rep?.goods_out.length && <div className="goods"><h5>Merce uscita</h5>
          <ul>{rep.goods_out.map((g, i) => <li key={i}><b>{g.pieces}</b> {g.item} <small>{g.size}</small></li>)}</ul></div>}
        {!!rep?.entries.length && <footer className="buttons"><button className="ghost" onClick={copy}>{copied ? "Copiato ✓" : "Copia il riepilogo"}</button></footer>}
      </section>

      <aside className="ledger-side">
        <section className="card"><h3>Da incassare</h3>
          {open?.sospesi.length ? <ul className="mini">{open.sospesi.map((r) => <li key={r.id}><span>{r.counterpart || r.item}</span><b>{fmt(r.amount)}</b><small>{sayDay(r.day, today)}</small></li>)}</ul>
            : <p className="muted">Nessun pagamento in sospeso.</p>}
          {!!open?.totale_sospeso && <footer>Totale: <b>{fmt(open.totale_sospeso)}</b></footer>}</section>
        <section className="card"><h3>Da fare</h3>
          {open?.da_fare.length ? <ul className="mini">{open.da_fare.map((r) => <li key={r.id}><span>{r.item}{r.size ? ` ${r.size.split(" ")[0]}` : ""}</span><b>{r.counterpart}</b><small>{sayDay(r.day, today)}</small></li>)}</ul>
            : <p className="muted">Niente in sospeso da consegnare.</p>}</section>
        <section className="card files"><h3>Documenti creati</h3>
          {files.length ? <ul className="mini">{files.map((f) => <li key={f.uri}><span>{f.title}</span>
            <button className="ghost" onClick={() => downloadFile(f.uri).catch((e) => setError((e as Error).message))}>Scarica</button></li>)}</ul>
            : <p className="muted">Quando NODO crea un PDF, un Excel o una presentazione, lo trovi qui.</p>}</section>
      </aside>
    </div>
  </div>;
}
