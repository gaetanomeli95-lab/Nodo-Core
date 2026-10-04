import { useCallback, useEffect, useState } from "react";
import { get, type NodoEvent, type Project, type SystemInfo, type Task, type VoiceConfig } from "./api";
import { CommandCenter } from "./components/CommandCenter";
import { StatePanel, TracePanel } from "./components/Panels";

export default function App() {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [voice, setVoice] = useState<VoiceConfig | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [trace, setTrace] = useState<NodoEvent[]>([]);
  const [activeContext, setActiveContext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    Promise.all([get<SystemInfo>("/api/v1/system"), get<Project[]>("/api/v1/projects"), get<Task[]>("/api/v1/tasks")])
      .then(([s, p, t]) => { setSystem(s); setProjects(p); setTasks(t); setError(null); })
      .catch((e) => setError(`API non raggiungibile: ${e.message}`));
  }, []);

  useEffect(() => { refresh(); get<VoiceConfig>("/api/v1/voice/config").then(setVoice).catch(() => {}); }, [refresh]);

  return (
    <main className="layout">
      <StatePanel system={system} projects={projects} tasks={tasks} activeContext={activeContext} />
      <div className="center">
        {error && <div className="error">{error}</div>}
        <CommandCenter voiceConfig={voice} onTrace={setTrace} onActiveContext={setActiveContext} onRequestDone={refresh} />
      </div>
      <TracePanel events={trace} system={system} />
    </main>
  );
}
