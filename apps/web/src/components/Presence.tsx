import type { VoiceState } from "./Voice";

type PresenceState = VoiceState | "WORKING";
type PresenceProps = {
  state: PresenceState;
  connected: boolean;
  engine: "live" | "browser";
  activity: string | null;
  partial: string;
  latest: string | null;
};

const STATES: Record<PresenceState, { title: string; note: string }> = {
  IDLE: { title: "Sono qui.", note: "Parlami normalmente. Possiamo ragionare o lavorare insieme." },
  CONNECTING: { title: "Mi collego.", note: "Sto ripristinando il collegamento vocale." },
  LISTENING: { title: "Ti ascolto.", note: "Continua pure, non devi usare comandi speciali." },
  TRANSCRIBING: { title: "Ti seguo.", note: "Sto riconoscendo quello che hai detto." },
  UNDERSTANDING: { title: "Ho capito.", note: "Metto in relazione la tua richiesta con il contesto." },
  THINKING: { title: "Ci sto ragionando.", note: "Sto valutando la risposta migliore." },
  ACTING: { title: "Sono al lavoro.", note: "Sto usando gli strumenti necessari." },
  WORKING: { title: "Sono al lavoro.", note: "Sto eseguendo la tua richiesta." },
  SPEAKING: { title: "Ti rispondo.", note: "Puoi interrompermi quando vuoi." },
  INTERRUPTED: { title: "Mi fermo.", note: "Dimmi pure come preferisci continuare." },
  WAITING_FOR_APPROVAL: { title: "Mi serve il tuo OK.", note: "Controlla la proposta prima di confermare." },
  ERROR: { title: "C'è un problema.", note: "La conversazione scritta resta disponibile." },
  CLOSED: { title: "Sessione chiusa.", note: "Puoi riaprire la conversazione." },
};

export function Presence({ state, connected, engine, activity, partial, latest }: PresenceProps) {
  const current = !connected && state !== "WORKING" ? "CONNECTING" : state;
  const copy = STATES[current];
  const phase = current.toLowerCase().replaceAll("_", "-");

  return (
    <section className={`presence presence--${phase}`} aria-label="Stato operativo di NODO">
      <div className="presence-heading">
        <span className="presence-brand">NODO <span>CORE</span></span>
        <span className="presence-channel">
          <i aria-hidden="true" />
          {connected ? (engine === "live" ? "Voce dal vivo" : "Voce browser") : "Ricollegamento"}
        </span>
      </div>
      <div className="presence-art" aria-hidden="true">
        <div className="presence-halo" />
        <div className="presence-ring presence-ring--outer" />
        <div className="presence-ring presence-ring--mid" />
        <div className="presence-ring presence-ring--inner" />
        <div className="presence-core">
          <span className="presence-core-letter">N</span>
        </div>
      </div>
      <div className="presence-caption">
        <p className="presence-kicker" aria-live="polite">{activity ? "ATTIVITÀ IN CORSO" : "PRESENZA OPERATIVA"}</p>
        <h2>{copy.title}</h2>
        <p className="presence-description">{activity || copy.note}</p>
        {partial && <p className="presence-heard" aria-label="Sto ascoltando">{partial}</p>}
        {!partial && latest && current !== "ACTING" && current !== "THINKING" && current !== "WORKING" && (
          <p className="presence-last" aria-label="Ultima risposta di NODO">{latest}</p>
        )}
      </div>
    </section>
  );
}
