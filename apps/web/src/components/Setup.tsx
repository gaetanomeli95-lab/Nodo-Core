import { useEffect, useState } from "react";
import { get, type SetupCheck } from "../api";

const ICON: Record<string, string> = { ok: "✓", missing: "–", error: "✕", optional: "○", warning: "!" };

export function SetupView() {
  const [checks, setChecks] = useState<SetupCheck[] | null>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = () => {
    setChecks(null); setErr(null);
    get<{ checks: SetupCheck[]; ready: boolean }>("/api/v1/setup/check")
      .then((r) => { setChecks(r.checks); setReady(r.ready); })
      .catch((e) => setErr((e as Error).message));
  };
  useEffect(load, []);
  return (
    <div className="view setup">
      <section className="card">
        <header>
          <div>
            <h3>Accessi</h3>
            <small>{checks ? (ready ? "Tutto pronto per il turno di notte." : "Manca qualcosa: segui le istruzioni qui sotto.") : "Verifico…"}</small>
          </div>
          <div className="buttons"><button className="ghost" onClick={load}>Ricontrolla</button></div>
        </header>
        {err && <div className="note">{err}</div>}
        <ul className="setup-list">
          {checks?.map((c) => (
            <li key={c.name} className={c.status}>
              <span className="icon">{ICON[c.status]}</span>
              <div>
                <b>{c.name}</b>
                <p>{c.detail}</p>
                {c.how && <p className="how">{c.how}</p>}
              </div>
            </li>
          ))}
        </ul>
        <p className="muted">Le chiavi vanno nel file <code>apps/api/.env</code> (vedi <code>docs/ACCESSI.md</code>), poi riavvia NODO. Le chiavi restano nel tuo computer: NODO le usa solo per parlare con quei servizi.</p>
      </section>
    </div>
  );
}
