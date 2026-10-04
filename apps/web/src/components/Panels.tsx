import type { NodoEvent, Project, SystemInfo, Task } from "../api";

export function StatePanel({ system, projects, tasks, activeContext }: { system: SystemInfo | null; projects: Project[]; tasks: Task[]; activeContext: string | null }) {
  const today = new Date().toISOString().slice(0, 10);
  const overdue = tasks.filter((t) => t.due_date && t.due_date < today && t.status !== "DONE");
  const blocked = tasks.filter((t) => t.status === "BLOCKED");
  const names = Object.fromEntries(projects.map((p) => [p.id, p.name]));
  return (
    <aside className="panel left">
      <header>
        <h1>NODO <span>CORE</span></h1>
        {system && <div className="badges"><b>{system.mode}</b><i>{system.env}</i></div>}
      </header>
      {activeContext && <div className="context">Contesto attivo: <b>{activeContext}</b></div>}
      <h2>Pulse</h2>
      <ul className="pulse">
        <li><b>{overdue.length}</b> attività in ritardo</li>
        <li><b>{blocked.length}</b> bloccate</li>
        <li><b>{projects.filter((p) => p.status === "active").length}</b> progetti attivi</li>
      </ul>
      {(overdue.length > 0 || blocked.length > 0) && (
        <ul className="alerts">
          {[...blocked, ...overdue].slice(0, 4).map((t) => <li key={t.id}><span>{names[t.project_id || ""] || "—"}</span>{t.title}</li>)}
        </ul>
      )}
      <h2>Progetti</h2>
      <ul className="projects">
        {projects.map((p) => <li key={p.id}><span className={`prio p${p.priority}`}>{p.priority}</span>{p.name}<small>{p.kind}</small></li>)}
        {projects.length === 0 && <li className="muted">Nessun progetto. Crea il primo via API (vedi README).</li>}
      </ul>
    </aside>
  );
}

export function TracePanel({ events, system }: { events: NodoEvent[]; system: SystemInfo | null }) {
  const u = system?.usage;
  const shown = events.filter((e) => e.type !== "token");
  return (
    <aside className="panel right">
      <h2>Esecuzione</h2>
      <ol className="trace">
        {shown.length === 0 && <li className="muted">Qui vedrai intent, contesto, piano, agenti e strumenti di ogni richiesta.</li>}
        {shown.map((e, i) => (
          <li key={i} className={e.type.split(".")[0]}>
            <code>{e.type}</code>
            <span>{describe(e)}</span>
          </li>
        ))}
      </ol>
      <h2>Oggi</h2>
      {u ? (
        <ul className="usage">
          <li>Locale <b>{u.local_requests}</b></li>
          <li>Cloud gratuito <b>{u.cloud_requests - u.premium_requests}</b></li>
          <li>Premium <b>{u.premium_requests}</b></li>
          <li>Agenti <b>{u.agent_runs}</b></li>
          <li>Strumenti <b>{u.tool_executions}</b></li>
          <li>Token <b>{u.tokens}</b></li>
          <li>Spesa stimata <b>€{u.estimated_cost.toFixed(2)}</b></li>
        </ul>
      ) : <p className="muted">—</p>}
      {system && (
        <>
          <h2>Modelli</h2>
          <ul className="models">
            {system.models.map((m) => <li key={m.id} className={m.available ? "" : "off"}>{m.id}<small>{m.tier}{m.is_local ? " · local" : ""}</small></li>)}
          </ul>
          <p className="muted">Voce: STT {system.voice.stt.name} ({system.voice.stt.location}) · TTS {system.voice.tts.name} · GitHub: {system.github}</p>
        </>
      )}
    </aside>
  );
}

function describe(e: NodoEvent): string {
  switch (e.type) {
    case "intent": return `${e.name}${(e.entity as { name?: string } | null)?.name ? " → " + (e.entity as { name: string }).name : ""}${e.from_context ? " (dal contesto)" : ""}`;
    case "plan": return (e.steps as string[]).join(" › ");
    case "context": return `${(e.sources as string[]).length} fonti · ${String(e.sensitivity)} · ${Object.entries(e.size as Record<string, number>).filter(([, v]) => v).map(([k, v]) => `${k}:${v}`).join(" ")}`;
    case "step": return `${e.step} ${e.status}`;
    case "agent.started": case "agent.finished": return `${e.agent}${e.status ? " " + e.status : ""}`;
    case "tool.started": case "tool.finished": return `${e.tool}.${e.action}${e.ok === false ? " ✗" : ""}`;
    case "done": return `${e.provider}/${e.model} · ${e.confidence}`;
    case "error": return String(e.message);
    default: return "";
  }
}
