import { useEffect, useRef, useState } from "react";
import { downloadFile, get, post, streamCommand, type NodoEvent, type VoiceConfig, type VoiceEvent } from "../api";
import { useVoiceSession } from "./Voice";
import type { PresenceMode } from "./Presence";
import { Pulse } from "./Pulse";
import { Viewer, type ViewItem } from "./Viewer";
import { Board } from "./holo/Board";
import { MindGraph } from "./holo/MindGraph";
import { Eyes } from "./holo/Eyes";
import { Palette, type Action } from "./holo/Palette";
import { Subtitles } from "./holo/Subtitles";

type Turn = { who: "user" | "nodo"; text: string; done?: boolean };
type Decision = { id: string; label: string; site?: string; preview?: string; pr?: string; sending?: boolean };
type Surface = { view: string; rows: Record<string, unknown>[]; summary: string };
const DOING: Record<string, string> = {
  night_report: "Rileggo il turno di notte", sites_status: "Guardo i tuoi siti", check_sites: "Controllo i siti",
  pensa: "Ci penso", pending_decisions: "Raccolgo le decisioni", decide: "Eseguo la tua decisione", request_site_change: "Preparo la modifica",
  projects: "Guardo i progetti", today_agenda: "Metto a fuoco le priorità", create_task: "Segno l'attività",
  complete_task: "Aggiorno l'attività", remember: "Aggiorno la memoria", recall: "Cerco nella memoria", save_draft: "Salvo la bozza",
  clients: "Guardo i clienti", create_client: "Creo il cliente", create_project: "Creo il progetto",
  update_project: "Aggiorno il progetto", list_tasks: "Rileggo le attività", update_task: "Aggiorno l'attività",
  set_reminder: "Imposto il promemoria", list_reminders: "Guardo i promemoria", cancel_reminder: "Annullo il promemoria",
  notify_me: "Ti scrivo su Telegram", forget: "Tolgo dalla memoria", list_site_files: "Guardo il codice del sito",
  read_site_file: "Leggo un file del sito", site_commits: "Guardo gli ultimi aggiornamenti", site_deployments: "Guardo i deploy",
  check_url: "Controllo l'indirizzo", fetch_url: "Leggo la pagina", web_search: "Cerco sul web", list_files: "Guardo i file",
  ledger_add: "Segno nel registro", ledger_update: "Correggo il registro", ledger_report: "Faccio i conti",
  ledger_open: "Guardo sospesi e consegne", ledger_prices: "Guardo il listino", ledger_close_day: "Chiudo la giornata",
  show_on_screen: "Te lo mostro", read_photo: "Guardo la foto", ledger_stats: "Guardo i numeri del negozio", close_screen: "Chiudo", nodo_updates: "Guardo i miei aggiornamenti", create_document: "Preparo il documento", now: "Guardo l'ora",
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

export function CommandCenter({ voiceConfig, onTrace, onActiveContext, onRequestDone, onOpenTab }: {
  voiceConfig: VoiceConfig | null; onTrace: (e: NodoEvent[]) => void;
  onActiveContext: (name: string | null) => void; onRequestDone: () => void; onOpenTab?: (tab: string) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [diary, setDiary] = useState(false);
  const [writing, setWriting] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [surface, setSurfaceRaw] = useState<Surface | null>(null);
  const [viewing, setViewing] = useState<ViewItem | null>(null);
  const [eyes, setEyes] = useState<"camera" | "screen" | null>(null);
  const [boardRev, setBoardRev] = useState(0);
  const onOpenTabRef = useRef(onOpenTab);
  onOpenTabRef.current = onOpenTab;
  /** «Sul tavolo» cards, plus the screen commands: show a file big, open a tab, close what is shown. */
  const setSurface = (next: Surface | null) => {
    if (next?.view === "viewer") {
      const row = next.rows[0] || {};
      setViewing({ title: String(row.title || "File"), url: String(row.url || ""), kind: String(row.kind || "text") });
      onOpenTabRef.current?.("command");                 // the viewer lives on the conversation screen
      if (row.download) setSurfaceRaw({ ...next, view: "files" });
      return;
    }
    if (next?.view === "close") { setViewing(null); setSurfaceRaw(null); setEyes(null); return; }
    if (next?.view === "board") { setBoardRev((n) => n + 1); return; }
    if (next?.view === "tab") { setViewing(null); onOpenTabRef.current?.(String(next.rows[0]?.tab || "command")); return; }
    setSurfaceRaw(next);
  };
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
          spokenGreeting.current = g.text; void voiceRef.current.sayAloud(g.text);
        };
        // The NODO app window allows sound at once (it is opened with autoplay allowed): she greets you as the
        // particles assemble. In a normal browser tab sound waits for the first click or key.
        const once = () => { window.removeEventListener("pointerdown", once); window.removeEventListener("keydown", once); speakIt(); };
        const tryNow = () => {
          const probe = new AudioContext();
          const allowed = probe.state === "running";
          void probe.close();
          if (allowed || (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation?.hasBeenActive) speakIt();
          else { window.addEventListener("pointerdown", once); window.addEventListener("keydown", once); }
        };
        setTimeout(tryNow, 1700);                                         // after the particles have assembled
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
        : "Voce veloce attiva: Gemini ascolta e parla dal vivo, per tutto il resto pensa Claude. Vale dalla prossima volta che apri la voce.");
    } catch (e) { setError((e as Error).message); }
    finally { setModeBusy(false); }
  };
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  /** A photo or a document handed to NODO: uploaded among its files, then NODO is told to look at it. */
  const handOver = async (file: File, question?: string) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const t = localStorage.getItem("nodo_token");
    try {
      const r = await fetch("/api/v1/files/upload", { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((d as { detail?: string }).detail || `caricamento: ${r.status}`);
      const isPhoto = /\.(png|jpe?g|webp)$/i.test((d as { name: string }).name);
      const note = (question ?? input).trim();
      setInput("");
      await send(`[Ti ho caricato ${isPhoto ? "una foto" : "un file"}: ${(d as { name: string }).name}] ${note || (isPhoto ? "Guardala e dimmi cosa c'è." : "Dagli un'occhiata.")}`);
    } catch (e) { setError((e as Error).message); }
  };
  const actions: Action[] = [
    { id: "talk", label: voice.liveOn ? "Chiudi la voce" : "Parla con NODO", hint: "voce", run: () => voice.toggleMic() },
    { id: "write", label: "Scrivi un pensiero", hint: "testo", run: () => setWriting(true) },
    { id: "camera", label: "Guarda con la camera", hint: "occhi", run: () => setEyes("camera") },
    { id: "screen", label: "Guarda il mio schermo", hint: "occhi", run: () => setEyes("screen") },
    { id: "photo", label: "Mostrale una foto o un file", hint: "carica", run: () => fileInput.current?.click() },
    { id: "recap", label: "Riepilogo di oggi del negozio", hint: "registro", run: () => void send("Fammi il riepilogo di oggi del negozio") },
    { id: "close", label: "Chiudi la giornata", hint: "registro", run: () => void send("Chiudiamo la giornata") },
    { id: "month", label: "Com'è andato il mese?", hint: "numeri", run: () => void send("Com'è andato il negozio negli ultimi 30 giorni?") },
    { id: "ledger", label: "Apri il Registro", hint: "scheda", run: () => onOpenTabRef.current?.("ledger") },
    { id: "night", label: "Apri la Notte", hint: "scheda", run: () => onOpenTabRef.current?.("night") },
    { id: "sites", label: "Apri i Siti", hint: "scheda", run: () => onOpenTabRef.current?.("sites") },
    { id: "memory", label: "Apri la Memoria", hint: "scheda", run: () => onOpenTabRef.current?.("memory") },
    { id: "setup", label: "Apri gli Accessi", hint: "scheda", run: () => onOpenTabRef.current?.("setup") },
    { id: "precise", label: "Voce precisa (Claude)", hint: "voce", run: () => void setVoiceMode("precise") },
    { id: "fast", label: "Voce veloce (Gemini)", hint: "voce", run: () => void setVoiceMode("auto") },
    { id: "diary", label: diary ? "Chiudi il diario" : "Apri il diario", hint: "conversazione", run: () => setDiary(!diary) },
    { id: "new", label: "Nuova conversazione", hint: "conversazione", run: () => voice.setConversation(null) },
  ];
  const lastUser = [...turns].reverse().find((t) => t.who === "user")?.text;
  const lastNodo = turns.at(-1)?.who === "nodo" ? turns.at(-1)?.text : undefined;
  const answer = (choice: "approve" | "reject") => {
    if (!decision || decision.sending) return;
    voice.answer(decision.id, choice); setDecision({ ...decision, sending: true });
  };
  const mode: PresenceMode = voice.recovering ? "reconnect" : decision ? "approve" : voice.audioPlaying ? "speak"
    : activity ? "act" : busy || ["THINKING", "UNDERSTANDING", "ACTING"].includes(voice.state) ? "think"
    : voice.microphoneOn ? "listen" : voice.state === "TRANSCRIBING" ? "think" : "rest";
  const stuck = mode === "think" || mode === "act";
  useEffect(() => {                                   // never «thinking» for minutes: stop, say so, listen again
    if (!stuck) return;
    const t = setTimeout(() => {
      voice.interrupt(); setActivity(null); setBusy(false);
      setError("Ci stavo mettendo troppo e mi sono fermata. Ripetimelo pure, ti ascolto.");
    }, 75_000);
    return () => clearTimeout(t);
  }, [stuck]);  // eslint-disable-line react-hooks/exhaustive-deps
  const title = { rest: "Sono qui.", listen: "Ti ascolto.", think: "Metto a fuoco.", act: "Ci sto lavorando.",
    speak: "Un passo avanti.", approve: "Decidiamo insieme.", reconnect: "Riprendo il filo." }[mode];
  const subtitle = mode === "reconnect" ? "Sto riconnettendo la voce. Attendi un momento prima di parlare."
    : activity || (mode === "rest" ? "Un pensiero, un progetto, una cosa da risolvere."
      : mode === "approve" ? "Guarda la proposta. La scelta resta tua."
      : mode === "listen" ? (voice.engine === "api" ? "Parla, poi fai una pausa: ti rispondo in qualche secondo." : "Prenditi il tuo tempo.")
      : mode === "speak" ? (voice.engine === "api" ? "Per interrompermi tocca Interrompi." : "Puoi interrompermi quando vuoi.")
      : "Collego la tua richiesta alle cose che contano.");

  return <section className={`conversation-room ${diary ? "with-diary" : ""} ${dragging ? "dragging" : ""}`}
    onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
    onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
    onDrop={(e) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files?.[0]; if (f) void handOver(f); }}>
    {dragging && <div className="drop-hint">Lascia qui la foto o il documento: NODO lo guarda</div>}
    <Viewer item={viewing} onClose={() => setViewing(null)} />
    <Eyes source={eyes} onClose={() => setEyes(null)} onShot={(f, q) => void handOver(f, q || "Guarda e dimmi cosa vedi.")} />
    <Palette actions={actions} onAsk={(t) => void send(t)} />
    {!diary && <Board revision={boardRev} />}
    <div className="voice-stage">
      <MindGraph mode={mode} sampleLevel={voice.sampleLevel} heard={voice.partial || lastUser} said={lastNodo}
        activity={activity} revision={turns.length + boardRev} />
      <div className="stage-topline"><span className="eyebrow">IL TUO SPAZIO DI LAVORO</span>
        <button className={`quiet-button ${diary ? "selected" : ""}`} aria-expanded={diary} onClick={() => setDiary(!diary)}>Diario <span>↗</span></button></div>
      <div className="presence-copy" aria-live="polite"><span className={`presence-state ${mode}`}><i />
        {!voice.connected ? "COLLEGAMENTO IN CORSO" : mode === "rest" ? "PRONTO QUANDO VUOI" : { listen: "IN ASCOLTO", think: "STO PENSANDO", act: "AL LAVORO", speak: "NODO PARLA", approve: "UNA TUA DECISIONE", reconnect: "RICONNESSIONE" }[mode]}</span>
        <h2>{title}</h2><p>{subtitle}</p></div>
      <div className="holo-stage-core mind-spacer" />
      {activity && <div className="holo-chip" role="status"><i />{activity}</div>}
      {!decision && !surface && <Subtitles user={lastUser} nodo={lastNodo} partial={voice.partial} />}
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
        <button className="quiet-button" title="Mostra a NODO una foto (scontrino, bolla, etichetta) o un documento" onClick={() => fileInput.current?.click()}>Mostrale una foto</button>
        <button className="quiet-button" title="NODO guarda con la camera" onClick={() => setEyes("camera")}>Occhi</button>
        <button className="quiet-button holo-k" title="Tutti i comandi (Ctrl+K)" onClick={() => window.dispatchEvent(new Event("nodo:palette"))}>⌘K</button>
        <input ref={fileInput} type="file" hidden accept="image/*,.pdf,.docx,.xlsx,.pptx,.csv,.txt"
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void handOver(f); }} />
      </div>
      {writing && <form className="thought-composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <input autoFocus aria-label="Scrivi a NODO" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Da dove cominciamo?" />
        <button disabled={!input.trim() || busy}>Invia ↗</button></form>}
      <footer className="stage-footnote"><span>{voice.microphoneOn ? "MICROFONO ATTIVO" : "ATTIVA LA VOCE PER COMINCIARE"}</span><span>{voice.engine === "live" ? "Conversazione dal vivo · pensa Claude"
        : voiceConfig?.stt.location === "local" && voiceConfig?.tts.location === "local" ? "Voce locale sul tuo PC · pause di qualche secondo"
        : voice.engine === "api" ? (precise ? "Voce precisa · risponde Claude · pause di qualche secondo" : "Voce Gemini · pause di qualche secondo") : "Voce del browser"}</span>
        <span className="voice-mode" role="group" aria-label="Tipo di voce">
          <button className={!precise ? "on" : ""} disabled={modeBusy || voice.liveOn} title="Dal vivo: Gemini ascolta e parla, il cervello Claude pensa e ricorda" onClick={() => setVoiceMode("auto")}>Veloce</button>
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
