import { useEffect, useState } from "react";
import { get, post, type SetupCheck } from "../api";

const ICON: Record<string, string> = { ok: "✓", missing: "–", error: "✕", optional: "○", warning: "!" };

// keys you can paste here: saved into apps/api/.env and activated without restarting NODO
const KEYS = [
  { name: "NODO_GUARD_WORD", label: "Parola d'ordine — a voce, regole e persone cambiano solo se la dici (scrivila qui, non a voce davanti agli altri)" },
  { name: "NODO_GEMINI_API_KEY", label: "Gemini — voce dal vivo + cervello gratis (aistudio.google.com)" },
  { name: "NODO_GROQ_API_KEY", label: "Groq — cervello veloce gratis (console.groq.com)" },
  { name: "NODO_NVIDIA_API_KEY", label: "NVIDIA — cervello di riserva gratis, tanta quota (build.nvidia.com)" },
  { name: "NODO_OPENROUTER_API_KEY", label: "OpenRouter — modelli gratuiti, pochi al giorno senza credito (openrouter.ai)" },
  { name: "NODO_CEREBRAS_API_KEY", label: "Cerebras — cervello di riserva (cloud.cerebras.ai)" },
  { name: "NODO_MISTRAL_API_KEY", label: "Mistral — cervello di riserva (console.mistral.ai)" },
  { name: "NODO_GITHUB_TOKEN", label: "GitHub — pull request e turno di notte" },
  { name: "NODO_VERCEL_TOKEN", label: "Vercel — pubblicazione dei siti" },
  { name: "NODO_CALENDAR_ICS_URL", label: "Calendario — indirizzo segreto iCal di Google Calendar (sola lettura)" },
  { name: "NODO_TELEGRAM_BOT_TOKEN", label: "Telegram — il briefing sul telefono" },
  { name: "NODO_TELEGRAM_CHAT_ID", label: "Telegram — chat id" },
  { name: "NODO_OPENAI_API_KEY", label: "OpenAI API — a consumo" },
  { name: "NODO_ANTHROPIC_API_KEY", label: "Anthropic API — a consumo" },
];

export function SetupView() {
  const [checks, setChecks] = useState<SetupCheck[] | null>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [keyName, setKeyName] = useState(KEYS[0].name);
  const [keyValue, setKeyValue] = useState("");
  const [keyMsg, setKeyMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const saveKey = async () => {
    if (!keyValue.trim() || saving) return;
    setSaving(true); setKeyMsg(null); setErr(null);
    try {
      await post("/api/v1/setup/key", { name: keyName, value: keyValue.trim() });
      window.dispatchEvent(new Event("nodo:access-updated"));
      setKeyValue("");
      setKeyMsg("Chiave salvata e attiva. Ricontrollo gli accessi…");
      load();
    } catch (e) {
      setKeyMsg((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
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
        <div className="keyform">
          <h4>Incolla una chiave: si attiva subito, senza riavviare</h4>
          <div className="keyrow">
            <select value={keyName} onChange={(e) => setKeyName(e.target.value)}>
              {KEYS.map((k) => <option key={k.name} value={k.name}>{k.label}</option>)}
            </select>
            <input type="password" value={keyValue} placeholder="incolla qui la chiave"
                   onChange={(e) => setKeyValue(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && saveKey()} />
            <button onClick={saveKey} disabled={!keyValue.trim() || saving}>
              {saving ? "Salvo…" : "Salva e attiva"}
            </button>
          </div>
          {keyMsg && <p className="note">{keyMsg}</p>}
        </div>
        <p className="muted">La chiave finisce nel file <code>apps/api/.env</code> e resta nel tuo computer: NODO la usa solo per parlare con quei servizi. Puoi anche scriverla a mano nel file (vedi <code>docs/ACCESSI.md</code>).</p>
      </section>
    </div>
  );
}
