import { useCallback, useEffect, useState } from "react";
import {
  act, clock, get, timeAgo,
  type Briefing, type Fix, type NightAction, type NightRun, type NightStatus, type Pending,
} from "../api";

const PREVIEW: Record<string, string> = {
  ok: "anteprima funzionante", failed: "anteprima NON funzionante", protected: "anteprima protetta (controllala tu)",
  pending: "anteprima in costruzione", unknown: "anteprima non verificata",
};
const ACTION_IT: Record<string, string> = {
  rollback: "rollback", redeploy: "build rilanciata", fix_pr: "correzione", merge: "pubblicazione", verify: "verifica",
  close_pr: "PR chiusa", draft: "bozza", escalate: "segnalazione", promote: "pubblicazione",
};

/** Reads Italian text aloud with the browser's own voice (free, on-device). */
export function speakIt(text: string) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/^- /gm, ""));
  u.lang = "it-IT";
  const v = window.speechSynthesis.getVoices().find((x) => x.lang.startsWith("it"));
  if (v) u.voice = v;
  window.speechSynthesis.speak(u);
}

export function NightView({ onChanged }: { onChanged: () => void }) {
  const [b, setB] = useState<Briefing | null>(null);
  const [status, setStatus] = useState<NightStatus | null>(null);
  const [fixes, setFixes] = useState<Fix[]>([]);
  const [actions, setActions] = useState<NightAction[]>([]);
  const [runs, setRuns] = useState<NightRun[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async (fresh = false) => {
    try {
      const [bb, st, fx, ac, rn] = await Promise.all([
        get<Briefing>(`/api/v1/night/briefing${fresh ? "?fresh=1" : ""}`), get<NightStatus>("/api/v1/night/status"),
        get<Fix[]>("/api/v1/fixes"), get<NightAction[]>("/api/v1/night/actions?limit=30"), get<NightRun[]>("/api/v1/night/runs?limit=12"),
      ]);
      setB(bb); setStatus(st); setFixes(fx); setActions(ac); setRuns(rn);
    } catch (e) { setMsg(`Non riesco a leggere il turno di notte: ${(e as Error).message}`); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  // while a run is in progress, refresh every 4 s
  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(() => { void load(true); }, 4000);
    return () => clearInterval(t);
  }, [status?.running, load]);

  const run = async (kind: "light" | "manual") => {
    setBusy(kind); setMsg(null);
    try {
      await act("/api/v1/night/run", { kind });
      setMsg(kind === "light" ? "Giro di controllo avviato." : "Turno completo avviato: controlli, correzioni e verifiche.");
      setTimeout(() => void load(true), 1200);
    } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(null); }
  };

  const decide = async (p: Pending, approve: boolean) => {
    setBusy(p.approval_id); setMsg(null);
    try {
      const out = await act<{ message?: string; result?: { message?: string } }>(
        p.fix_id ? `/api/v1/fixes/${p.fix_id}/${approve ? "approve" : "reject"}` : `/api/v1/approvals/${p.approval_id}/${approve ? "approve" : "reject"}`);
      setMsg(out.message || out.result?.message || "Fatto.");
      await load(true); onChanged();
    } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(null); }
  };

  const sendBriefing = async () => {
    setBusy("send");
    try {
      const r = await act<{ sent: boolean; notifier: string | null }>("/api/v1/night/briefing/send");
      setMsg(r.sent ? "Briefing inviato su Telegram." : "Nessun canale di notifica configurato (vedi Accessi).");
    } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(null); }
  };

  const fixById = Object.fromEntries(fixes.map((f) => [f.id, f]));
  const running = status?.running;

  return (
    <div className="view night">
      <section className="card briefing">
        <header>
          <div>
            <h3>Turno di notte</h3>
            <small>
              {status ? `${status.window.start}–${status.window.end} · briefing alle ${status.window.briefing_time}` : "—"}
              {status?.demo && <b className="tag demo">DEMO</b>}
              {status && !status.scheduler && <b className="tag warn">pianificatore spento</b>}
            </small>
          </div>
          <div className="buttons">
            <button className="ghost" onClick={() => b && speakIt(b.text)} disabled={!b}>Ascolta</button>
            <button className="ghost" onClick={() => void sendBriefing()} disabled={busy === "send"}>Invia al telefono</button>
            <button className="ghost" onClick={() => void run("light")} disabled={!!busy || !!running}>Controlla ora</button>
            <button onClick={() => void run("manual")} disabled={!!busy || !!running}>Turno completo</button>
          </div>
        </header>
        {running && <div className="running"><span className="pulse-dot" />Giro in corso dalle {clock(running.started_at)}…</div>}
        {msg && <div className="note">{msg}</div>}
        {b ? (
          <div className="briefing-text">
            {b.text.split("\n").map((line, i) => (
              <p key={i} className={line.startsWith("- ") ? "item" : line.startsWith("Da decidere") ? "decide" : ""}>{line.replace(/^- /, "")}</p>
            ))}
          </div>
        ) : <p className="muted">Carico il briefing…</p>}
        {b && <footer className="muted">{b.stored ? "Briefing del mattino" : "Composto adesso"} · {b.runs} giri · {b.sites_checked}/{b.sites_total} siti{b.cost_usd > 0 ? ` · spesa AI $${b.cost_usd.toFixed(2)}` : ""}</footer>}
      </section>

      <section className="card">
        <h3>Da decidere {b && b.pending.length > 0 && <span className="count">{b.pending.length}</span>}</h3>
        {b && b.pending.length === 0 && <p className="muted">Niente da approvare. Quando preparo una correzione o un'azione delicata, la trovi qui.</p>}
        {b?.pending.map((p) => {
          const f = p.fix_id ? fixById[p.fix_id] : undefined;
          return (
            <article key={p.approval_id} className="decision">
              <div className="what">
                <b>{p.label}</b>
                {f && <p>{f.diagnosis}</p>}
                {f && (
                  <div className="meta">
                    <span className={`preview ${f.preview_status}`}>{PREVIEW[f.preview_status] || f.preview_status}</span>
                    {f.pr_url && <a href={f.pr_url} target="_blank" rel="noreferrer">PR #{f.pr_number}</a>}
                    {f.preview_url && <a href={f.preview_url} target="_blank" rel="noreferrer">apri anteprima</a>}
                    <span>confidenza {f.confidence}</span>
                    {f.model && <span>{f.model}</span>}
                  </div>
                )}
                {f && f.diff && (
                  <details>
                    <summary>Modifiche ({f.files.map((x) => `${x.path} +${x.added}/-${x.removed}`).join(", ")})</summary>
                    <pre className="diff">{f.diff.split("\n").map((l, i) => (
                      <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}>{l + "\n"}</span>
                    ))}</pre>
                  </details>
                )}
              </div>
              <div className="choose">
                <button onClick={() => void decide(p, true)} disabled={!!busy}>{p.fix_id ? "Pubblica" : "Approva"}</button>
                <button className="ghost danger" onClick={() => void decide(p, false)} disabled={!!busy}>Rifiuta</button>
              </div>
            </article>
          );
        })}
      </section>

      <div className="two">
        <section className="card">
          <h3>Cosa ho fatto</h3>
          <ol className="timeline">
            {actions.length === 0 && <li className="muted">Ancora nessuna azione.</li>}
            {actions.map((a) => (
              <li key={a.id} className={a.status}>
                <time>{timeAgo(a.created_at)}</time>
                <span className="kind">{ACTION_IT[a.action] || a.action}</span>
                <span>{a.site ? <b>{a.site}: </b> : null}{a.summary}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="card">
          <h3>Giri di controllo</h3>
          <ul className="runs">
            {runs.length === 0 && <li className="muted">Nessun giro ancora.</li>}
            {runs.map((r) => {
              const s = r.summary as Record<string, number>;
              return (
                <li key={r.id} className={r.status}>
                  <span>{new Date(r.started_at).toLocaleString("it-IT", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span>
                  <span className="kind">{{ night: "notte", light: "rapido", manual: "manuale", briefing: "briefing" }[r.kind] || r.kind}</span>
                  <span>{r.kind === "briefing" ? "composto" : r.status === "running" ? "in corso…" :
                    r.status === "failed" ? `errore: ${r.error ?? ""}` : `${s.sites ?? 0} siti · ${s.ok ?? 0} ok · ${(s.warning ?? 0) + (s.critical ?? 0)} da guardare`}</span>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}
