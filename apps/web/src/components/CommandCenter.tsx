import { useEffect, useRef, useState } from "react";
import { streamCommand, type NodoEvent, type VoiceConfig } from "../api";
import { speakText, stopSpeaking, VoiceButton, type VoiceState } from "./VoiceButton";

export type Turn = { role: "user" | "nodo"; text: string; requestId?: string; meta?: Record<string, unknown> };

type Props = {
  voiceConfig: VoiceConfig | null;
  onTrace: (events: NodoEvent[]) => void;
  onActiveContext: (name: string | null) => void;
  onRequestDone: () => void;
};

export function CommandCenter({ voiceConfig, onTrace, onActiveContext, onRequestDone }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("IDLE");
  const [speakReplies, setSpeakReplies] = useState(false);
  const conv = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [turns]);

  const send = async (text: string, channel: "text" | "voice" = "text") => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setInput("");
    const events: NodoEvent[] = [];
    setTurns((t) => [...t, { role: "user", text }, { role: "nodo", text: "" }]);
    if (channel === "voice") setVoiceState("THINKING");
    abort.current = new AbortController();
    try {
      await streamCommand({ text, conversation_id: conv.current, channel }, (e) => {
        events.push(e);
        if (e.type === "token") {
          setTurns((t) => { const c = [...t]; c[c.length - 1] = { ...c[c.length - 1], text: c[c.length - 1].text + (e.delta as string) }; return c; });
        } else if (e.type === "done") {
          conv.current = e.conversation_id as string;
          setTurns((t) => { const c = [...t]; c[c.length - 1] = { role: "nodo", text: e.text as string, requestId: e.request_id,
            meta: { provider: e.provider, model: e.model, intent: e.intent, confidence: e.confidence, agents: e.agents } }; return c; });
          const ent = events.find((x) => x.type === "intent")?.entity as { name?: string } | null;
          if (ent?.name) onActiveContext(ent.name);
          if (channel === "voice" || speakReplies) {
            setVoiceState("SPEAKING");
            speakText(e.text as string, voiceConfig, () => setVoiceState("IDLE"));
          } else setVoiceState("IDLE");
        } else if (e.type === "error") {
          setTurns((t) => { const c = [...t]; c[c.length - 1] = { role: "nodo", text: `Errore: ${e.message}` }; return c; });
          setVoiceState("IDLE");
        }
        onTrace([...events]);
      }, abort.current.signal);
    } catch (err) {
      setTurns((t) => { const c = [...t]; c[c.length - 1] = { role: "nodo", text: `Connessione fallita: ${(err as Error).message}` }; return c; });
      setVoiceState("IDLE");
    } finally { setBusy(false); onRequestDone(); }
  };

  const stop = () => { abort.current?.abort(); stopSpeaking(null); setVoiceState("IDLE"); setBusy(false); };

  return (
    <section className="command">
      <div className="turns">
        {turns.length === 0 && (
          <div className="hint">
            <p>Prova: <em>"Mostrami i miei progetti"</em>, <em>"Fammi il punto su Prosperya"</em>, <em>"Cosa devo fare oggi?"</em>,
              <em>"Prepara il prossimo contenuto per SoftComfort"</em>, <em>"Controlla lo stato tecnico di PB CARe"</em>.</p>
          </div>
        )}
        {turns.map((t, i) => (
          <article key={i} className={`turn ${t.role}`}>
            <div className="who">{t.role === "user" ? "Tu" : "NODO"}</div>
            <div className="text">{t.text || (busy && i === turns.length - 1 ? <span className="cursor" /> : "")}</div>
            {t.meta && (
              <div className="meta">
                {String(t.meta.intent)} · {String(t.meta.provider)}/{String(t.meta.model)} · {String(t.meta.confidence)}
                {Array.isArray(t.meta.agents) && t.meta.agents.length > 0 && <> · agenti: {(t.meta.agents as string[]).join(", ")}</>}
              </div>
            )}
          </article>
        ))}
        <div ref={bottom} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <VoiceButton config={voiceConfig} state={voiceState} setState={setVoiceState} onTranscript={(t) => send(t, "voice")} />
        <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="Scrivi a NODO…" rows={1}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
        {busy || voiceState === "SPEAKING" ? <button type="button" className="ghost" onClick={stop}>Ferma</button>
          : <button type="submit" disabled={!input.trim()}>Invia</button>}
        <label className="toggle"><input type="checkbox" checked={speakReplies} onChange={(e) => setSpeakReplies(e.target.checked)} /> voce</label>
      </form>
      <div className={`voice-state ${voiceState.toLowerCase()}`}>{voiceState}</div>
    </section>
  );
}
