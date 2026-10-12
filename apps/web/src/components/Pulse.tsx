import { useEffect, useState } from "react";
import { get, type PulseSnapshot } from "../api";

export function Pulse({ revision, disabled, onInvestigate }: {
  revision: number;
  disabled: boolean;
  onInvestigate: (question: string) => void;
}) {
  const [pulse, setPulse] = useState<PulseSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    get<PulseSnapshot>("/api/v1/pulse")
      .then((snapshot) => {
        if (!active) return;
        setPulse(snapshot);
        setError(null);
      })
      .catch(() => {
        if (active) setError("Non riesco a leggere le priorità dal database.");
      });
    return () => { active = false; };
  }, [revision, refresh]);

  return (
    <section className="pulse" aria-label="Priorità registrate da NODO">
      <div className="pulse-header">
        <div>
          <span className="pulse-eyebrow">NEL RADAR · DATI REGISTRATI</span>
          <p className="pulse-headline">{pulse?.headline || (error ?? "Controllo le priorità registrate…")}</p>
        </div>
        <button type="button" className="pulse-refresh" onClick={() => setRefresh((n) => n + 1)}
                title="Aggiorna le priorità dal database" aria-label="Aggiorna priorità">↻</button>
      </div>
      {error && <p className="pulse-error" role="status">{error}</p>}
      {pulse && (
        <>
          <div className="pulse-stats" aria-label="Riepilogo attività">
            <span>{pulse.counts.open_tasks} attività aperte</span>
            <span>{pulse.counts.open_incidents} problemi registrati</span>
            <span>{pulse.counts.pending_approvals} decisioni in attesa</span>
          </div>
          {pulse.items.length > 0 && (
            <div className="pulse-items">
              {pulse.items.slice(0, 3).map((item) => (
                <article className={`pulse-item pulse-item--${item.severity}`} key={item.id}>
                  <div className="pulse-item-copy">
                    <span className="pulse-source">{item.kind === "task" ? "ATTIVITÀ" :
                      item.kind === "incident" ? "SITO · INCIDENTE" : "APPROVAZIONE"}
                      {item.project ? ` · ${item.project}` : ""}
                    </span>
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                  </div>
                  <button type="button" className="pulse-action" disabled={disabled}
                          onClick={() => onInvestigate(item.prompt)}
                          aria-label={`Chiedi a NODO di approfondire: ${item.title}`}>Esamina ↗</button>
                </article>
              ))}
            </div>
          )}
          <p className="pulse-footnote">
            {pulse.night.runs
              ? `Ultimo turno di notte: ${pulse.night.sites_checked} siti controllati nei dati della finestra.`
              : "Nessun controllo notturno registrato per l'ultima finestra."}
            {" "}Le osservazioni non sono verifiche in tempo reale.
          </p>
        </>
      )}
    </section>
  );
}
