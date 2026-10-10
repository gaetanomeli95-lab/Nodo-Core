import { useCallback, useEffect, useState } from "react";
import { act, get, patch, timeAgo, type KeyPage, type Site, type SiteDetail } from "../api";

const STATUS_IT: Record<string, string> = { ok: "regolare", warning: "attenzione", critical: "problema", unknown: "da controllare" };
const CHECK_IT: Record<string, string> = {
  deploy: "Deploy", uptime: "Online", pages: "Pagine chiave", speed: "Velocità", runtime: "Errori server", ssl: "Certificato",
  security: "Sicurezza", ci: "CI",
};

function pagesToText(p: KeyPage[]): string {
  return p.map((x) => (x.expect ? `${x.path} = ${x.expect}` : x.path)).join("\n");
}

function textToPages(t: string): KeyPage[] {
  return t.split("\n").map((l) => l.trim()).filter((l) => l.startsWith("/")).map((l) => {
    const [path, ...rest] = l.split("=");
    const expect = rest.join("=").trim();
    return { path: path.trim(), expect: expect || null };
  });
}

function SiteCard({ site, onChanged }: { site: Site; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<SiteDetail | null>(null);
  const [pages, setPages] = useState(pagesToText(site.key_pages));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (open) get<SiteDetail>(`/api/v1/sites/${site.id}`).then(setDetail).catch(() => setDetail(null));
  }, [open, site.id, site.last_checked_at]);

  const save = async (body: Partial<Site>) => {
    setBusy(true); setMsg(null);
    try { await patch(`/api/v1/sites/${site.id}`, body); setMsg("Salvato."); onChanged(); }
    catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  };
  const check = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await act<{ summary: Record<string, number> }>("/api/v1/night/run", { kind: "light", site_ids: [site.id], wait: true });
      setMsg(r.summary.critical ? "Controllato: c'è un problema." : r.summary.warning ? "Controllato: qualcosa da guardare." : "Controllato: tutto ok.");
      onChanged();
    } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  };
  const flag = (k: string, def: boolean) => (site.policy?.[k] ?? def);

  return (
    <article className={`site ${site.status} ${site.active ? "" : "inactive"}`}>
      <header onClick={() => setOpen(!open)}>
        <span className={`dot ${site.status}`} title={STATUS_IT[site.status]} />
        <div className="name">
          <b>{site.name}</b>
          <a href={site.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{site.url.replace(/^https?:\/\//, "")}</a>
        </div>
        <div className="state">
          <span>{STATUS_IT[site.status] || site.status}</span>
          <small>{timeAgo(site.last_checked_at)}</small>
        </div>
      </header>
      {site.status_note && site.status !== "ok" && <p className="note-line">{site.status_note}</p>}
      {open && (
        <div className="site-body">
          <div className="facts">
            <span>Vercel: {site.vercel_project_id ? "collegato" : "no"}</span>
            <span>Repo: {site.repo_owner ? `${site.repo_owner}/${site.repo_name} (${site.default_branch})` : "non collegato"}</span>
            <span>Ultima versione sana: {site.last_good_deployment_id ? site.last_good_deployment_id.slice(0, 12) : "non ancora nota"}</span>
          </div>
          {detail && detail.checks.length > 0 && (
            <ul className="checks">
              {Object.values(Object.fromEntries(detail.checks.slice().reverse().map((c) => [c.check, c]))).map((c) => (
                <li key={c.id} className={c.status}><b>{CHECK_IT[c.check] || c.check}</b><span>{c.summary}</span></li>
              ))}
            </ul>
          )}
          {detail && detail.incidents.length > 0 && (
            <>
              <h4>Problemi recenti</h4>
              <ul className="incidents">
                {detail.incidents.slice(0, 6).map((i) => (
                  <li key={i.id} className={`${i.severity} ${i.status}`}>
                    <span>{i.title}</span>
                    <small>{i.status === "resolved" ? `risolto${i.resolution ? `: ${i.resolution}` : ""}` : i.status === "mitigated" ? "tamponato" : i.status === "ignored" ? "ignorato" : `aperto · ${i.occurrences}×`}</small>
                    {(i.status === "open" || i.status === "mitigated") && (
                      <button className="link" onClick={() => void act(`/api/v1/incidents/${i.id}/ignore`).then(onChanged)}>ignora</button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          <h4>Pagine chiave <small>(una per riga: <code>/percorso</code> oppure <code>/percorso = testo che deve comparire</code>)</small></h4>
          <textarea rows={3} value={pages} onChange={(e) => setPages(e.target.value)} placeholder={"/contatti = Contattaci\n/prenota"} />
          <div className="policy">
            <label><input type="checkbox" checked={flag("rollback", true)} onChange={(e) => void save({ policy: { ...site.policy, rollback: e.target.checked } })} /> rollback automatico</label>
            <label><input type="checkbox" checked={flag("redeploy", true)} onChange={(e) => void save({ policy: { ...site.policy, redeploy: e.target.checked } })} /> rilancia build fallite per errori di rete</label>
            <label><input type="checkbox" checked={flag("fix_prs", true)} onChange={(e) => void save({ policy: { ...site.policy, fix_prs: e.target.checked } })} /> prepara correzioni (pubblico solo col tuo ok)</label>
            <label><input type="checkbox" checked={site.active} onChange={(e) => void save({ active: e.target.checked })} /> controlla questo sito</label>
          </div>
          <div className="buttons">
            <button onClick={() => void save({ key_pages: textToPages(pages) })} disabled={busy}>Salva pagine</button>
            <button className="ghost" onClick={() => void check()} disabled={busy}>{busy ? "Controllo…" : "Controlla adesso"}</button>
            {msg && <span className="muted">{msg}</span>}
          </div>
        </div>
      )}
    </article>
  );
}

export function SitesView({ onChanged }: { onChanged: () => void }) {
  const [sites, setSites] = useState<Site[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", url: "https://", pages: "" });
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    get<Site[]>("/api/v1/sites?include_inactive=true").then(setSites).catch((e) => setMsg((e as Error).message));
  }, []);
  useEffect(load, [load]);
  const changed = () => { load(); onChanged(); };

  const discover = async () => {
    setMsg(null);
    try {
      const r = await act<{ created: string[] }>("/api/v1/sites/discover");
      setMsg(r.created.length ? `Importati da Vercel: ${r.created.join(", ")}` : "Nessun progetto nuovo su Vercel.");
      changed();
    } catch (e) { setMsg((e as Error).message); }
  };
  const add = async () => {
    setMsg(null);
    try {
      await act("/api/v1/sites", { name: form.name, url: form.url, key_pages: textToPages(form.pages) });
      setForm({ name: "", url: "https://", pages: "" }); setAdding(false); changed();
    } catch (e) { setMsg(`Non salvato: ${(e as Error).message}`); }
  };

  const counts = { critical: 0, warning: 0, ok: 0, unknown: 0 } as Record<string, number>;
  (sites || []).filter((s) => s.active).forEach((s) => { counts[s.status] = (counts[s.status] || 0) + 1; });

  return (
    <div className="view sites">
      <section className="card">
        <header>
          <div>
            <h3>Siti</h3>
            <small>{sites ? `${sites.filter((s) => s.active).length} controllati · ${counts.ok} ok · ${counts.warning} attenzione · ${counts.critical} problemi` : "…"}</small>
          </div>
          <div className="buttons">
            <button className="ghost" onClick={() => void discover()}>Importa da Vercel</button>
            <button onClick={() => setAdding(!adding)}>{adding ? "Chiudi" : "Aggiungi sito"}</button>
          </div>
        </header>
        {msg && <div className="note">{msg}</div>}
        {adding && (
          <div className="add-site">
            <input placeholder="Nome (es. PB-CARe)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="https://www.esempio.it" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
            <textarea rows={2} placeholder={"Pagine chiave (facoltative)\n/contatti = Contattaci"} value={form.pages} onChange={(e) => setForm({ ...form, pages: e.target.value })} />
            <button onClick={() => void add()} disabled={!form.name || !/^https?:\/\/.+/.test(form.url)}>Salva</button>
          </div>
        )}
      </section>
      {sites && sites.length === 0 && (
        <section className="card empty">
          <p>Non ci sono ancora siti. Il modo più veloce: inserisci il token Vercel (scheda <b>Accessi</b>) e premi <b>Importa da Vercel</b>: NODO prende tutti i tuoi progetti, i domini e i repository collegati.</p>
        </section>
      )}
      <div className="site-list">
        {sites?.map((s) => <SiteCard key={s.id} site={s} onChanged={changed} />)}
      </div>
    </div>
  );
}
