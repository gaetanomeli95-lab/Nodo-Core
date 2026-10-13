import { useCallback, useEffect, useState } from "react";
import { get, type NodoEvent, type Pending, type Project, type Site, type SystemInfo, type Task, type VoiceConfig } from "./api";
import { CommandCenter } from "./components/CommandCenter";
import { LedgerView } from "./components/Ledger";
import { MemoryView } from "./components/Memory";
import { NightView } from "./components/Night";
import { StatePanel, TracePanel } from "./components/Panels";
import { SetupView } from "./components/Setup";
import { SitesView } from "./components/Sites";

type Tab = "command" | "ledger" | "night" | "sites" | "memory" | "setup";

function initialTab(): Tab {
  try {
    const fromHash = location.hash.slice(1) as Tab;
    if (["command", "ledger", "night", "sites", "memory", "setup"].includes(fromHash)) return fromHash;
    return "command";
  } catch { return "command"; }
}

export default function App() {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [voice, setVoice] = useState<VoiceConfig | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [trace, setTrace] = useState<NodoEvent[]>([]);
  const [activeContext, setActiveContext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [details, setDetails] = useState(false);

  const refresh = useCallback(() => {
    Promise.all([get<SystemInfo>("/api/v1/system"), get<Project[]>("/api/v1/projects"), get<Task[]>("/api/v1/tasks")])
      .then(([s, p, t]) => { setSystem(s); setProjects(p); setTasks(t); setError(null); })
      .catch((e) => setError(`API non raggiungibile: ${e.message}`));
    get<Site[]>("/api/v1/sites").then(setSites).catch(() => {});
    get<Pending[]>("/api/v1/night/pending").then(setPending).catch(() => {});
  }, []);

  useEffect(() => { refresh(); get<VoiceConfig>("/api/v1/voice/config").then(setVoice).catch(() => {}); }, [refresh]);
  useEffect(() => {
    const reloadVoice = () => { void get<VoiceConfig>("/api/v1/voice/config").then(setVoice).catch(() => {}); };
    window.addEventListener("nodo:access-updated", reloadVoice);
    return () => window.removeEventListener("nodo:access-updated", reloadVoice);
  }, []);
  useEffect(() => { const t = setInterval(refresh, 60_000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => { try { history.replaceState(null, "", `#${tab}`); } catch { /* ignore */ } }, [tab]);
  useEffect(() => {
    const onHash = () => setTab(initialTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const critical = sites.filter((s) => s.status === "critical").length;
  const tabs: [Tab, string, number][] = [
    ["command", "Presenza", 0], ["ledger", "Registro", 0], ["night", "Notte", pending.length], ["sites", "Siti", critical], ["memory", "Memoria", 0],
    ["setup", "Accessi", 0],
  ];

  return (
    <main className={`layout presence-layout ${details ? "show-details" : ""}`}>
      <StatePanel system={system} projects={projects} tasks={tasks} activeContext={activeContext} sites={sites}
                  pending={pending.length} onOpen={(t) => setTab(t as Tab)} />
      <div className="center">
        {error && <div className="error">{error}</div>}
        <nav className="tabs">
          <a className="nodo-wordmark" href="#command" aria-label="NODO, torna alla conversazione">nodo<span>CORE</span></a>
          {tabs.map(([id, label, n]) => (
            <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
              {label}{n > 0 && <span className="count">{n}</span>}
            </button>
          ))}
          <button className="details-toggle" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? "Chiudi dettagli" : "Dettagli"}</button>
        </nav>
        <div className={tab === "command" ? "tab-body" : "tab-body hidden"}>
          <CommandCenter voiceConfig={voice} onTrace={setTrace} onActiveContext={setActiveContext} onRequestDone={refresh}
            onOpenTab={(t) => setTab((["command", "ledger", "night", "sites", "memory", "setup"].includes(t) ? t : "command") as Tab)} />
        </div>
        {tab === "ledger" && <div className="tab-body scroll"><LedgerView /></div>}
        {tab === "night" && <div className="tab-body scroll"><NightView onChanged={refresh} /></div>}
        {tab === "sites" && <div className="tab-body scroll"><SitesView onChanged={refresh} /></div>}
        {tab === "memory" && <div className="tab-body scroll"><MemoryView /></div>}
        {tab === "setup" && <div className="tab-body scroll"><SetupView /></div>}
      </div>
      <TracePanel events={trace} system={system} />
    </main>
  );
}
