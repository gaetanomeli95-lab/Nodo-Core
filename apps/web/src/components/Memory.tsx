import { useCallback, useEffect, useRef, useState } from "react";
import { act, get, timeAgo, type KnowledgeOverview, type KnowledgeSource, type KnowledgeHit } from "../api";

const STATUS_IT: Record<string, string> = {
  imported: "importato, da distillare", distilling: "sto leggendo…", done: "letto tutto", partial: "letto in parte",
  failed: "errore", no_model: "serve un cervello", stopped: "fermato",
};
const KIND_IT: Record<string, string> = { chatgpt: "ChatGPT", claude: "Claude", file: "file" };

async function upload(file: File): Promise<{ import: Record<string, number>; distilling: boolean; source: KnowledgeSource }> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("distill_now", "true");
  const t = localStorage.getItem("nodo_token");
  const r = await fetch("/api/v1/knowledge/import", { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `import: ${r.status}`);
  return data;
}

function SourceRow({ s, onChanged }: { s: KnowledgeSource; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const st = s.stats || {};
  const total = st.conversations ?? 0;
  const done = st.distilled ?? 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const run = async (path: string, method: "POST" | "DELETE" = "POST") => {
    setBusy(true);
    try {
      if (method === "DELETE") {
        if (!confirm("Rimuovo le conversazioni importate da questo file? I fatti già distillati restano in memoria come archiviati.")) return;
        const t = localStorage.getItem("nodo_token");
        const r = await fetch(path, { method, headers: t ? { Authorization: `Bearer ${t}` } : {} });
        if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { detail?: string }).detail || `${r.status}`);
      } else await act(path);
      onChanged();
    } catch (e) { alert((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <li className={`source ${s.status}`}>
      <div className="source-head">
        <b>{KIND_IT[s.kind] || s.kind}</b>
        <span className="muted">{s.label}</span>
        <small>{timeAgo(s.created_at)}</small>
      </div>
      <div className="source-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${pct}%` }} />
      </div>
      <p className="source-stats">
        {total} conversazioni · {st.chunks ?? 0} passaggi · lette {done}{st.remaining ? ` (ne restano ${st.remaining})` : ""} ·
        {" "}{st.facts ?? 0} fatti nuovi{st.skipped ? `, ${st.skipped} già noti o deboli` : ""} —{" "}
        <b>{s.running ? "sto leggendo…" : STATUS_IT[s.status] || s.status}</b>
      </p>
      {s.note && !s.running && <p className="note">{s.note}</p>}
      <div className="buttons">
        {s.running
          ? <button className="ghost" disabled={busy} onClick={() => run(`/api/v1/knowledge/sources/${s.id}/stop`)}>Ferma</button>
          : (s.status !== "done" && <button disabled={busy} onClick={() => run(`/api/v1/knowledge/sources/${s.id}/distill`)}>{done ? "Continua a leggere" : "Leggi e distilla"}</button>)}
        {!s.running && <button className="ghost danger" disabled={busy} onClick={() => run(`/api/v1/knowledge/sources/${s.id}`, "DELETE")}>Rimuovi</button>}
      </div>
    </li>
  );
}

export function MemoryView() {
  const [data, setData] = useState<KnowledgeOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [path, setPath] = useState("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    get<KnowledgeOverview>("/api/v1/knowledge").then((d) => { setData(d); setErr(null); }).catch((e) => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);
  const running = !!data?.sources.some((s) => s.running);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [running, load]);

  const describe = (r: { import: Record<string, number>; distilling: boolean }) =>
    `Importate ${r.import.new} conversazioni nuove${r.import.updated ? `, ${r.import.updated} aggiornate` : ""}${r.import.unchanged ? `, ${r.import.unchanged} già presenti` : ""}.` +
    (r.distilling ? " Ora le leggo in background e distillo i fatti: puoi continuare a parlare con NODO." : "");

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setUploading(true); setMsg(null); setErr(null);
    try { setMsg(describe(await upload(f))); load(); }
    catch (e) { setErr((e as Error).message); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const onPath = async () => {
    if (!path.trim()) return;
    setUploading(true); setMsg(null); setErr(null);
    try { setMsg(describe(await act("/api/v1/knowledge/import-path", { path: path.trim() }))); setPath(""); load(); }
    catch (e) { setErr((e as Error).message); }
    finally { setUploading(false); }
  };
  const search = async () => {
    if (!q.trim()) { setHits(null); return; }
    try { setHits((await get<{ results: KnowledgeHit[] }>(`/api/v1/knowledge/search?q=${encodeURIComponent(q.trim())}`)).results); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <div className="view memory">
      <section className="card">
        <header>
          <div>
            <h3>Memoria</h3>
            <small>
              {data ? `${data.conversations} conversazioni importate · ${data.facts} fatti distillati` : "Carico…"}
            </small>
          </div>
          <div className="buttons"><button className="ghost" onClick={load}>Aggiorna</button></div>
        </header>
        {err && <div className="note">{err}</div>}
        <p className="muted">
          Porta dentro NODO quello che hai già raccontato ad altri assistenti. Le conversazioni restano sul tuo computer,
          cercabili; il cervello di NODO le legge e ne ricava fatti su di te, sui clienti e sui progetti, ognuno con la sua fonte.
        </p>
        <ol className="howto">
          <li><b>ChatGPT</b>: Impostazioni → Controlli dati → <i>Esporta dati</i>. Arriva una mail con uno zip: caricalo qui così com'è.</li>
          <li><b>Claude</b>: Impostazioni → Privacy → <i>Esporta dati</i>. Stesso procedimento, zip o conversations.json.</li>
        </ol>
        <div className="keyrow">
          <input ref={fileRef} type="file" accept=".zip,.json,application/zip,application/json" disabled={uploading}
                 onChange={(e) => onFile(e.target.files?.[0])} />
        </div>
        <div className="keyrow" style={{ marginTop: 8 }}>
          <input type="text" value={path} placeholder="oppure incolla il percorso del file sul disco (per export molto grandi)"
                 onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onPath()} disabled={uploading} />
          <button onClick={onPath} disabled={!path.trim() || uploading}>{uploading ? "Importo…" : "Importa"}</button>
        </div>
        {msg && <p className="note">{msg}</p>}
      </section>

      {data && data.sources.length > 0 && (
        <section className="card">
          <header><div><h3>Import</h3><small>Ogni file importato, con quanto ne ho letto.</small></div></header>
          <ul className="sources">{data.sources.map((s) => <SourceRow key={s.id} s={s} onChanged={load} />)}</ul>
        </section>
      )}

      {data && data.conversations > 0 && (
        <section className="card">
          <header><div><h3>Cerca nelle vecchie conversazioni</h3><small>La stessa ricerca che NODO fa quando gli chiedi «ricordi quando…».</small></div></header>
          <div className="keyrow">
            <input type="text" value={q} placeholder="parole chiave" onChange={(e) => setQ(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && search()} />
            <button onClick={search} disabled={!q.trim()}>Cerca</button>
          </div>
          {hits && (hits.length
            ? <ul className="hits">{hits.map((h) => (
                <li key={h.chunk_id}><b>{h.title}</b> <small>{KIND_IT[h.source] || h.source}{h.when ? ` · ${h.when}` : ""}</small><p>{h.snippet}</p></li>
              ))}</ul>
            : <p className="muted">Niente con queste parole.</p>)}
        </section>
      )}

      {data && data.recent_facts.length > 0 && (
        <section className="card">
          <header><div><h3>Cosa ho imparato</h3><small>Ultimi fatti distillati. Quelli incerti sono segnati come dedotti, non certi.</small></div></header>
          <ul className="facts-list">
            {data.recent_facts.map((f) => (
              <li key={f.id}><span className={`tag ${f.status === "INFERRED" ? "warn" : ""}`}>{f.status === "INFERRED" ? "dedotto" : "certo"}</span> {f.fact}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
