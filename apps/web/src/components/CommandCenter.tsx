import { useEffect, useRef, useState } from "react";
import { downloadFile, get, post, streamCommand, type NodoEvent, type VoiceConfig, type VoiceEvent } from "../api";
import { useVoiceSession } from "./Voice";
import { Presence, type PresenceMode } from "./Presence";
import { Pulse } from "./Pulse";

type Turn = { who: "user" | "nodo"; text: string; done?: boolean };
type Decision = { id: string; label: string; site?: string; preview?: string; pr?: string; sending?: boolean };
type Surface = { view: string; rows: Record<string, unknown>[]; summary: string };
const DOING: Record<string, string> = {
  night_report: "Rileggo il turno di notte", sites_status: "Guardo i tuoi siti", check_sites: "Controllo i siti",
  pending_decisions: "Raccolgo le decisioni", decide: "Eseguo la tua decisione", request_site_change: "Preparo la modifica",
  projects: "Guardo i progetti", today_agenda: "Metto a fuoco le priorità", create_task: "Segno l'attività",
  complete_task: "Aggiorno l'attività", remember: "Aggiorno la memoria", recall: "Cerco nella memoria", save_draft: "Salvo la bozza",
  clients: "Guardo i clienti", create_client: "Creo il cliente", create_project: "Creo il progetto",
  update_project: "Aggiorno il progetto", list_tasks: "Rileggo le attività", update_task: "Aggiorno l'attività",
  set_reminder: "Imposto il promemoria", list_reminders: "Guardo i promemoria", cancel_reminder: "Annullo il promemoria",
  notify_me: "Ti scrivo su Telegram", forget: "Tolgo dalla memoria", list_site_files: "Guardo il codice del sito",
  read_site_file: "Leggo un file del sito", site_commits: "Guardo gli ultimi aggiornamenti", site_deployments: "Guardo i deploy",
  check_url: "Controllo l'indirizzo", fetch_url: "Leggo la pagina", web_search: "Cerco sul web", list_files: "Guardo i file",
  ledger_add: "Segno nel registro", ledger_update: "Correggo il registro", ledger_report: "Faccio i conti",
  ledger_open: "Guardo sospesi e consegne", create_document: "Preparo il documento", now: "Guardo l'ora",
  calendar_events: "Guardo il calendario", ssl_status: "Controllo i certificati",
  read_file: "Apro il file", list_drafts: "Guardo le bozze", update_draft: "Aggiorno la bozza", start_work: "Avvio il lavoro",
  nodo_health: "Controllo i miei accessi", sync_sites: "Importo i siti", create_file: "Preparo il file",
  generate_image: "Disegno l'immagine", search_knowledge: "Cerco nelle vecchie conversazioni", read_knowledge: "Rileggo la conversazione",
};
const TITLES: Record<string, string> = { sites_status: "I tuoi siti", today_agenda: "Da qui si comincia",
  projects: "I progetti sul tavolo", night_report: "Mentre non c'eri", files: "File pronto",
  ledger: "Il registro", ledger_add: "Segnato nel registro", ledger_open: "Ancora aperto" };
const STATUS: Record<string, string> = { ok: "Regolare", warning: "Da controllare", critical: "Richiede attenzione",
  unknown: "Da verificare", open: "Da fare", in_progress: "In corso", blocked: "In attesa", active: "Attivo", done: "Completato" };
const FILE_LINK = /(\/api\/v1\/files\/[\w.%-]+)/g;
/** NODO's replies mention files as /api/v1/files/<name>: show them as download buttons. */
function Linkified({ text, onError }: { text: string; onError: (m: string) => void }) {
  const parts = text.split(FILE_LINK);
  return <>{parts.map((p, i) => i % 2 ? <button key={i} className="file-chip" onClick={() => downloadFile(p).catch((e) => onError((e as Error).message))}>
    ↓ {decodeURIComponent(p.split("/").pop() || "file")}</button> : <span key={i}>{p}</span>)}</>;
}
const loadConv = () => { try { return localStorage.getItem("nodo.conversation"); } catch { return null; } };
const safeUrl = (s?: string) => { try { const u = new URL(s || ""); return /^https?:$/.test(u.protocol) ? u.href : undefined; } catch { return undefined; } };

export function CommandCenter({ voiceConfig, onTrace, onActiveContext, onRequestDone }: {
  voiceConfig: VoiceConfig | null; onTrace: (e: NodoEvent[]) => void;
  onActiveContext: (name: string | null) => void; onRequestDone: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [diary, setDiary] = useState(false);
  const [writing, setWriting] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [surface, setSurface] = useState<Surface | null>(null);
  const [pulseRevision, setPulseRevision] = useState(0);   // refresh the radar after each turn
  const conv = useRef(loadConv());
  const journal = useRef<HTMLDivElement>(null);
  const activeTools = useRef(new Set<string>());

  const rememberConv = (id: unknown) => {
    if (typeof id !== "string" && id !== null) return;
    conv.current = id;
    try { if (id) localStorage.setItem("nodo.conversation", id); else localStorage.removeItem("nodo.conversation"); } catch { /* private mode */ }
  };
  const addDelta = (delta: string) => setTurns((ts) => {
    const last = ts[ts.length - 1];
    return last?.who === "nodo" && !last.done
      ? [...ts.slice(0, -1), { ...last, text: last.text + delta }]
      : [...ts, { who: "nodo", text: delta }];
  });
  const endReply = (e: Record<string, unknown>) => {
    rememberConv(e.conversation_id);
    setBusy(false);
    setPulseRevision((n) => n + 1);
    if (typeof e.text === "string") setTurns((ts) => {
      const next: Turn = { who: "nodo", text: e.text as string, done: true };
      return ts.at(-1)?.who === "nodo" && !ts.at(-1)?.done ? [...ts.slice(0, -1), next] : [...ts, next];
    });
  };
  const onEvent = (e: VoiceEvent) => {
    onTrace([e as unknown as NodoEvent]);
    if (e.conversation_id !== undefined) rememberConv(e.conversation_id);
    if (e.type === "voice.conversation" && e.conversation_id === null) {
      setTurns([]); setSurface(null); setError(null);
    }
    if ((e.type === "voice.session.started" || e.type === "voice.engine") && e.reason === "quota") {
      setError("Gemini Live ha raggiunto un limite di quota. Puoi continuare con la voce del browser; verifica le quote del progetto in Google AI Studio.");
    }
    if (e.type === "nodo.intent" && e.entity_name) onActiveContext(e.entity_name as string);
    if (e.type === "voice.turn.done") { setBusy(false); onRequestDone(); }
    if (e.type === "voice.interrupted") {
      setTurns((ts) => ts.map((t) => t.done ? t : { ...t, done: true }));
      setBusy(false);
    }
    if (e.type === "nodo.step" || e.type === "tool.started" || e.type === "tool.finished") {
      const name = String(e.step || e.tool || "").replace(/^tool:/, "");
      if (e.status === "running" || e.type === "tool.started") activeTools.current.add(name);
      else activeTools.current.delete(name);
      const current = Array.from(activeTools.current).at(-1);
      setActivity(current ? DOING[current] || "Ci lavoro" : null);
    }
    if (e.type === "ui.present") setSurface({ view: String(e.view), rows: e.rows as Surface["rows"] || [], summary: String(e.summary || "") });
    if (e.type === "approval.required") setDecision({ id: String(e.approval_id), label: String(e.label || "Conferma questa azione"),
      site: e.site as string, preview: safeUrl(e.preview_url as string), pr: safeUrl(e.pr_url as string) });
    if (e.type === "approval.resolved") { setDecision(null); onRequestDone(); }
    if (e.type === "approval.ignored") { setDecision((d) => d ? { ...d, sending: false } : null); setError("La conferma non è più valida. Chiedi a NODO di mostrarti le decisioni aggiornate."); }
    if (e.type === "voice.connection" && e.status === "connected") setError(null);
  };
  const voice = useVoiceSession(voiceConfig, {
    onEvent, onUserText: (text) => setTurns((ts) => [...ts, { who: "user", text, done: true }]),
    onNodoStart: () => {}, onNodoDelta: addDelta, onNodoDone: endReply,
    onError: (message) => { setError(message); setBusy(false); },
  });
  useEffect(() => {
    if (!conv.current) return;
    get<{ role: string; content: string }[]>(`/api/v1/conversations/${conv.current}/messages`)
      .then((ms) => setTurns((ts) => ts.length ? ts : ms.slice(-30).filter((m) => ["user", "nodo"].includes(m.role))
        .map((m) => ({ who: m.role as Turn["who"], text: m.content, done: true })))).catch(() => {});
  }, []);
  // The first words of the day: once per day NODO opens the conversation by itself and reads it aloud
  const greeted = useRef(false);
  const spokenGreeting = useRef<string | null>(null);
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  useEffect(() => {
    if (greeted.current) return;
    greeted.current = true;
    const today = new Date().toLocaleDateString("sv-SE");
    let last: string | null = null;
    try { last = localStorage.getItem("nodo.greeted"); } catch { /* private mode */ }
    if (last === today) return;
    post<{ text: string; conversation_id: string }>("/api/v1/greeting", { conversation_id: conv.current })
      .then((g) => {
        try { localStorage.setItem("nodo.greeted", today); } catch { /* private mode */ }
        rememberConv(g.conversation_id);
        voiceRef.current.setConversation(g.conversation_id);
        setTurns((ts) => [...ts, { who: "nodo", text: g.text, done: true }]);
        const speakIt = () => {
          if (spokenGreeting.current === g.text || voiceRef.current.microphoneOn) return;
          spokenGreeting.current = g.text; voiceRef.current.speak(g.text);
        };
        // browsers only allow speech after a first touch of the page: do it right away or at the first click/key
        if ((navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation?.hasBeenActive) speakIt();
        else {
          const once = () => { window.removeEventListener("pointerdown", once); window.removeEventListener("keydown", once); speakIt(); };
          window.addEventListener("pointerdown", once); window.addEventListener("keydown", once);
        }
      }).catch(() => {});
  }, []);
  useEffect(() => { if (diary) journal.current?.scrollTo(0, journal.current.scrollHeight); }, [turns, diary]);

  const send = async (requested?: string) => {
    const text = (requested ?? input).trim();
    if (!text || busy) return;
    if (requested === undefined) setInput("");   // investigating a Pulse item must not erase a drafted message
    setBusy(true); setError(null);
    if (voice.engine !== "browser" && voice.connected) {   // live/api: typed text is answered by voice too
      await voice.sendText(text).catch((e: Error) => { setError(e.message); setBusy(false); }); return;
    }
    setTurns((ts) => [...ts, { who: "user", text, done: true }]);
    try {
      await streamCommand({ text, conversation_id: conv.current }, (e) => {
        onTrace([e]);
        if (e.type === "token") addDelta(String(e.delta));
        if (e.type === "done" || e.type === "response") {
          endReply(e);
          if (typeof e.conversation_id === "string") voice.setConversation(e.conversation_id);
        }
        if (e.type === "step") setActivity(e.status === "running" ? DOING[String(e.step).replace("tool:", "")] || "Ci lavoro" : null);
        if (e.type === "error") setError(String(e.message));
        if (e.type === "ui.present") setSurface({ view: String(e.view), rows: (e.rows as Surface["rows"]) || [], summary: String(e.summary || "") });
      });
      onRequestDone();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); setActivity(null); }
  };
  const precise = (voiceConfig?.live_reason || "").startsWith("voce precisa");
  const [modeBusy, setModeBusy] = useState(false);
  const setVoiceMode = async (value: "auto" | "precise") => {
    if (modeBusy || (value === "precise") === precise) return;
    setModeBusy(true);
    try {
      await post("/api/v1/setup/key", { name: "NODO_VOICE_ENGINE", value });
      window.dispatchEvent(new Event("nodo:access-updated"));      // App reloads the voice configuration
      setError(value === "precise" ? "Voce precisa attiva: risponde Claude. Vale dalla prossima volta che apri la voce."
        : "Voce veloce attiva: risponde Gemini dal vivo. Vale dalla prossima volta che apri la voce.");
    } catch (e) { setError((e as Error).message); }
    finally { setModeBusy(false); }
  };
  const answer = (choice: "approve" | "reject") => {
    if (!decision || decision.sending) return;
    voice.answer(decision.id, choice); setDecision({ ...decision, sending: true });
  };
  const mode: PresenceMode = voice.recovering ? "reconnect" : decision ? "approve" : voice.audioPlaying ? "speak"
    : activity ? "act" : busy || ["THINKING", "UNDERSTANDING", "ACTING"].includes(voice.state) ? "think"
    : voice.microphoneOn ? "listen" : voice.state === "TRANSCRIBING" ? "think" : "rest";
  const title = { rest: "Sono qui.", listen: "Ti ascolto.", think: "Metto a fuoco.", act: "Ci sto lavorando.",
    speak: "Un passo avanti.", approve: "Decidiamo insieme.", reconnect: "Riprendo il filo." }[mode];
  const subtitle = mode === "reconnect" ? "Sto riconnettendo la voce. Attendi un momento prima di parlare."
    : activity || (mode === "rest" ? "Un pensiero, un progetto, una cosa da risolvere."
      : mode === "approve" ? "Guarda la proposta. La scelta resta tua."
      : mode === "listen" ? (voice.engine === "api" ? "Parla, poi fai una pausa: ti rispondo in qualche secondo." : "Prenditi il tuo tempo.")
      : mode === "speak" ? (voice.engine === "api" ? "Per interrompermi tocca Interrompi." : "Puoi interrompermi quando vuoi.")
      : "Collego la tua richiesta alle cose che contano.");

  return <section className={`conversation-room ${diary ? "with-diary" : ""}`}>
    <div className="voice-stage">
      <div className="stage-topline"><span className="eyebrow">IL TUO SPAZIO DI LAVORO</span>
        <button className={`quiet-button ${diary ? "selected" : ""}`} aria-expanded={diary} onClick={() => setDiary(!diary)}>Diario <span>↗</span></button></div>
      <div className="presence-copy" aria-live="polite"><span className={`presence-state ${mode}`}><i />
        {!voice.connected ? "COLLEGAMENTO IN CORSO" : mode === "rest" ? "PRONTO QUANDO VUOI" : { listen: "IN ASCOLTO", think: "STO PENSANDO", act: "AL LAVORO", speak: "NODO PARLA", approve: "UNA TUA DECISIONE", reconnect: "RICONNESSIONE" }[mode]}</span>
        <h2>{title}</h2><p>{subtitle}</p></div>
      <Presence mode={mode} sampleLevel={voice.sampleLevel} />
      {error && <div className="voice-notice" role="alert"><span>{error}</span><button aria-label="Chiudi avviso" onClick={() => setError(null)}>×</button></div>}
      {decision ? <section className="context-sheet decision-sheet" aria-label="Decisione in attesa">
        <div className="sheet-kicker">SERVE IL TUO OK {decision.site && `· ${decision.site}`}</div>
        <h3>{decision.label}</h3><p>La conferma vale solo per questa proposta.</p>
        <div className="sheet-actions">{decision.preview && <a href={decision.preview} target="_blank" rel="noreferrer">Apri anteprima ↗</a>}
          {decision.pr && <a href={decision.pr} target="_blank" rel="noreferrer">Guarda le modifiche ↗</a>}
          <button disabled={decision.sending || !voice.connected} onClick={() => answer("reject")}>Lascia stare</button>
          <button className="primary" disabled={decision.sending || !voice.connected} onClick={() => answer("approve")}>{decision.sending ? "Conferma in corso…" : "Conferma"}</button></div>
      </section> : surface && <section className="context-sheet" aria-label="Risultato del lavoro">
        <div className="sheet-heading"><span className="sheet-kicker">SUL TAVOLO</span><button aria-label="Riponi scheda" onClick={() => setSurface(null)}>×</button></div>
        <h3>{TITLES[surface.view] || "Il punto della situazione"}</h3>
        {surface.summary && <p>{surface.summary}</p>}
        <ul>{surface.rows.map((row, i) => <li key={i}><span>{String(row.title || row.name || "")}</span>
          {row.download && row.url ? <button className="primary" onClick={() => downloadFile(String(row.url)).catch((e) => setError((e as Error).message))}>Scarica ↓</button>
            : row.url ? <a href={String(row.url)} target="_blank" rel="noreferrer">Apri ↗</a>
                   : <small>{row.amount != null ? `${row.amount} €` : STATUS[String(row.status)] || String(row.project || row.day || "")}</small>}</li>)}</ul>
        {!surface.summary && !surface.rows.length && <p>Non ci sono elementi da mostrare.</p>}
      </section>}
      {!decision && !surface && mode === "rest" && <Pulse revision={pulseRevision} disabled={busy || !voice.connected}
        onInvestigate={(prompt) => { void send(prompt); }} />}
      {(voiceConfig?.stt.ready === false || voiceConfig?.tts.ready === false) && (
        <p className="voice-notice" role="status">
          La voce locale non è ancora installata. Esegui installa-voce-locale.bat e riavvia NODO.
        </p>
      )}
      <div className="voice-controls">
        <button className={`conversation-button ${voice.liveOn ? "active" : ""}`}
          disabled={!voice.connected || voice.recovering || voiceConfig?.stt.ready === false || voiceConfig?.tts.ready === false}
          onClick={voice.toggleMic}>
          <svg width="18" height="20" viewBox="0 0 18 22" fill="none" aria-hidden="true"><rect x="6" y="1" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.5" /><path d="M2 9v2a7 7 0 0 0 14 0V9M9 18v3M5 21h8" stroke="currentColor" strokeWidth="1.5" /></svg>
          {voice.liveOn ? "Microfono aperto" : voice.microphoneOn ? "Finito, invia" : "Parla con NODO"}</button>
        {(voice.audioPlaying || busy || activity) && <button className="quiet-button" onClick={() => voice.interrupt()}>Interrompi</button>}
        {voice.liveOn && <button className="quiet-button" onClick={voice.endConversation}>Chiudi voce</button>}
        <button className="quiet-button" aria-expanded={writing} onClick={() => setWriting(!writing)}>{writing ? "Chiudi testo" : "Scrivi un pensiero"}</button>
      </div>
      {writing && <form className="thought-composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <input autoFocus aria-label="Scrivi a NODO" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Da dove cominciamo?" />
        <button disabled={!input.trim() || busy}>Invia ↗</button></form>}
      <footer className="stage-footnote"><span>{voice.microphoneOn ? "MICROFONO ATTIVO" : "ATTIVA LA VOCE PER COMINCIARE"}</span><span>{voice.engine === "live" ? "Conversazione dal vivo"
        : voiceConfig?.stt.location === "local" && voiceConfig?.tts.location === "local" ? "Voce locale sul tuo PC · pause di qualche secondo"
        : voice.engine === "api" ? (precise ? "Voce precisa · risponde Claude · pause di qualche secondo" : "Voce Gemini · pause di qualche secondo") : "Voce del browser"}</span>
        <span className="voice-mode" role="group" aria-label="Tipo di voce">
          <button className={!precise ? "on" : ""} disabled={modeBusy || voice.liveOn} title="Risposte immediate (Gemini dal vivo)" onClick={() => setVoiceMode("auto")}>Veloce</button>
          <button className={precise ? "on" : ""} disabled={modeBusy || voice.liveOn} title="Risponde il cervello Claude: più preciso su conti e registro" onClick={() => setVoiceMode("precise")}>Precisa</button>
        </span></footer>
    </div>
    {diary && <aside className="conversation-diary"><header><span className="eyebrow">DIARIO</span><button className="quiet-button" onClick={() => setDiary(false)}>Chiudi</button></header>
      <p className="diary-intro">Le parole restano qui. Il lavoro continua al centro.</p>
      {!!turns.length && <button className="quiet-button" disabled={!voice.connected || busy || !!decision || !!activity || voice.audioPlaying || voice.microphoneOn}
        onClick={() => voice.setConversation(null)}>Nuova conversazione</button>}
      <div className="diary-entries" ref={journal}>{!turns.length && <p className="muted">La conversazione deve ancora cominciare.</p>}
        {turns.map((t, i) => <article key={i}><small>{t.who === "user" ? "TU" : "NODO"}</small><p><Linkified text={t.text} onError={setError} /></p></article>)}
        {voice.partial && <article className="partial-entry"><small>TI STO ASCOLTANDO</small><p>{voice.partial}</p></article>}</div>
    </aside>}
  </section>;
}
