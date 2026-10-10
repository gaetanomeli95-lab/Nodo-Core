import { useCallback, useEffect, useRef, useState } from "react";
import { get, streamCommand, type NodoEvent, type VoiceConfig } from "../api";
import { VoiceDock, useVoiceSession } from "./Voice";

type Turn = { who: "user" | "nodo"; text: string; meta?: string; doing?: string };

// what NODO is doing while it thinks (brain tools, nodo/brain/tools.py)
const DOING: Record<string, string> = {
  night_report: "guardo cosa è successo stanotte…", sites_status: "guardo lo stato dei siti…",
  check_sites: "controllo i siti adesso…", pending_decisions: "guardo cosa aspetta il tuo ok…",
  decide: "eseguo la tua decisione…", request_site_change: "avvio la modifica al sito…",
  projects: "guardo i progetti…", today_agenda: "metto in fila le priorità di oggi…",
  create_task: "segno l'attività…", complete_task: "aggiorno l'attività…", remember: "me lo segno…",
  recall: "cerco nella memoria…", save_draft: "salvo la bozza…",
};
const CONV_KEY = "nodo.conversation";
const loadConv = () => { try { return localStorage.getItem(CONV_KEY); } catch { return null; } };
const saveConv = (id: string | null) => { try { if (id) localStorage.setItem(CONV_KEY, id); else localStorage.removeItem(CONV_KEY); } catch { /* private mode */ } };
type PendingVoice = { approvalId: string; tool: string; action: string; params?: unknown };

export function CommandCenter({ voiceConfig, onTrace, onActiveContext, onRequestDone }: {
  voiceConfig: VoiceConfig | null;
  onTrace: (e: NodoEvent[]) => void;
  onActiveContext: (name: string | null) => void;
  onRequestDone: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [voicePending, setVoicePending] = useState<PendingVoice | null>(null);
  const pendingApproval = useRef(false);
  const conversationId = useRef<string | null>(loadConv());   // the brain remembers the conversation

  // reopening the panel shows the last messages of the ongoing conversation (also what NODO finished meanwhile)
  useEffect(() => {
    const id = conversationId.current;
    if (!id) return;
    get<{ role: string; content: string }[]>(`/api/v1/conversations/${id}/messages`)
      .then((ms) => setTurns((ts) => ts.length ? ts : ms.slice(-20).filter((m) => m.role === "user" || m.role === "nodo")
        .map((m) => ({ who: m.role === "user" ? "user" as const : "nodo" as const, text: m.content, meta: m.role === "nodo" ? "·" : undefined }))))
      .catch(() => { conversationId.current = null; saveConv(null); });
  }, []);
  const turnsRef = useRef<HTMLDivElement>(null);

  const appendNodo = useCallback((delta: string) =>
    setTurns((ts) => ts.map((t, i) => i === ts.length - 1 && t.who === "nodo" ? { ...t, text: t.text + delta } : t)), []);

  const voice = useVoiceSession(voiceConfig, {
    onEvent: (e) => {
      onTrace([e as unknown as NodoEvent]);
      if (e.type === "nodo.intent" && e.entity_name) onActiveContext(e.entity_name as string);
      if (e.type === "nodo.plan") pendingApproval.current = !!e.needs_approval;
      if (e.type === "nodo.turn.done" || e.type === "voice.turn.done") onRequestDone();
      if (e.type === "approval.required") {
        pendingApproval.current = true;
        setVoicePending({ approvalId: e.approval_id as string, tool: e.tool as string, action: e.action as string, params: e.params });
      }
      if (e.type === "approval.resolved") setVoicePending(null);
    },
    onUserText: (text) => setTurns((ts) => [...ts, { who: "user", text }]),
    onNodoStart: () => {
      setTurns((ts) => ts.length && ts[ts.length - 1].who === "nodo" && !ts[ts.length - 1].meta ? ts : [...ts, { who: "nodo", text: "" }]);
    },
    onNodoDelta: appendNodo,
    onNodoDone: (e) => {
      const text = e.text as string | undefined;
      const meta = `${e.provider ?? ""} · ${e.intent ?? ""}${e.interrupted ? " · interrotto" : ""}`;
      setTurns((ts) => {
        const next = [...ts];
        if (next.length && next[next.length - 1].who === "nodo") next[next.length - 1] = { who: "nodo", text: text ?? next[next.length - 1].text, meta };
        else if (text) next.push({ who: "nodo", text, meta });
        return next;
      });
    },
    onError: (msg) => setTurns((ts) => [...ts, { who: "nodo", text: `Errore voce: ${msg}`, meta: "error" }]),
  });

  const answer = (decision: "approve" | "reject") => {
    if (!voicePending) return;
    voice.answer(voicePending.approvalId, decision);
    setVoicePending(null);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput(""); setBusy(true);
    setTurns((ts) => [...ts, { who: "user", text }, { who: "nodo", text: "" }]);
    try {
      await streamCommand({ text, conversation_id: conversationId.current }, (ev) => {
        onTrace([ev]);
        if (ev.type === "token") appendNodo(ev.delta as string);
        if (ev.type === "step" && typeof ev.step === "string" && ev.step.startsWith("tool:")) {
          const doing = ev.status === "running" ? DOING[ev.step.slice(5)] ?? "ci lavoro…" : undefined;
          setTurns((ts) => ts.map((t, i) => i === ts.length - 1 && t.who === "nodo" ? { ...t, doing } : t));
        }
        if (ev.type === "done" && ev.conversation_id) {
          conversationId.current = ev.conversation_id as string;
          saveConv(conversationId.current);
        }
        if (ev.type === "done" || ev.type === "response") {
          setTurns((ts) => {
            const next = [...ts];
            next[next.length - 1] = { who: "nodo", text: ev.text as string, meta: `${ev.provider} · ${ev.intent}` };
            return next;
          });
        }
        if (ev.type === "error") {
          setTurns((ts) => {
            const next = [...ts];
            next[next.length - 1] = { who: "nodo", text: `Errore: ${String(ev.message)}`, meta: "error" };
            return next;
          });
        }
        if (ev.type === "intent" && ev.entity_name) onActiveContext(ev.entity_name as string);
        if (ev.type === "plan" || ev.type === "approval") pendingApproval.current = !!(ev.needs_approval ?? true);
      });
      onRequestDone();
    } catch (e) {
      setTurns((ts) => [...ts, { who: "nodo", text: `Errore: ${(e as Error).message}`, meta: "error" }]);
    } finally {
      setBusy(false);
    }
  };

  const newConversation = () => {
    conversationId.current = null;
    saveConv(null);
    setTurns([]);
  };

  const scroll = () => turnsRef.current?.scrollTo(0, turnsRef.current.scrollHeight);

  return (
    <section className="command" onClick={scroll}>
      <div className="turns" ref={turnsRef}>
        {!turns.length && (
          <p className="hint">
            Parla con NODO come con una persona: <em>"com'è andata stanotte?"</em>, <em>"cosa devo fare oggi?"</em>,{" "}
            <em>"sul sito di … cambia il titolo della home"</em> — via testo o tenendo premuto il microfono.
          </p>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn ${t.who}`}>
            <div className="who">{t.who === "nodo" ? "NODO" : "Tu"}</div>
            <div className="text">
              {t.text}
              {t.who === "nodo" && !t.text && t.doing && <span className="doing">{t.doing}</span>}
              {t.who === "nodo" && !t.meta && i === turns.length - 1 && (busy || voice.state === "THINKING" || voice.state === "ACTING" || voice.state === "SPEAKING") && <span className="cursor" />}
            </div>
            {t.meta && <div className="meta">{t.meta}</div>}
          </div>
        ))}
        {voicePending && (
          <div className="approval-banner">
            NODO chiede conferma: <b>{voicePending.tool}.{voicePending.action}</b>
            <button onClick={() => answer("approve")}>Conferma</button>
            <button className="ghost" onClick={() => answer("reject")}>Rifiuta</button>
          </div>
        )}
      </div>
      <div className={`voice-state ${voice.state.toLowerCase()}`}>
        {voice.connected ? `voce: ${voice.state}` : "voce: offline (WebSocket non connesso)"}
      </div>
      <div className="composer">
        <VoiceDock voice={voice} />
        <textarea
          rows={1}
          value={input}
          placeholder="Scrivi o detta… (Invio per inviare)"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }}
        />
        <button onClick={() => void send()} disabled={busy || !input.trim()}>Invia</button>
        {turns.length > 0 && !busy && (
          <button className="ghost" title="Inizia una nuova conversazione" onClick={newConversation}>Nuova</button>
        )}
      </div>
    </section>
  );
}
