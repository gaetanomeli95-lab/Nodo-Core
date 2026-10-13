import { useCallback, useEffect, useState } from "react";
import { get, post } from "../../api";

type Note = { id: string; text: string; color: string; created_at: string };

/** The board («lavagna»): notes NODO keeps in front of you. Tick to remove; NODO adds them by voice. */
export function Board({ revision }: { revision: number }) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [open, setOpen] = useState(true);
  const load = useCallback(() => { get<{ notes: Note[] }>("/api/v1/board").then((b) => setNotes(b.notes)).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load, revision]);
  useEffect(() => { const t = setInterval(load, 15_000); return () => clearInterval(t); }, [load]);
  if (!notes.length) return null;
  const done = async (id: string) => {
    setNotes((ns) => ns.filter((n) => n.id !== id));
    await post(`/api/v1/board/${id}/done`, {}).catch(load);
  };
  return <aside className={`holo-board ${open ? "" : "folded"}`} aria-label="Lavagna">
    <header><span className="eyebrow">LAVAGNA</span><span className="count">{notes.length}</span>
      <button aria-label={open ? "Riduci" : "Apri"} onClick={() => setOpen(!open)}>{open ? "–" : "+"}</button></header>
    {open && <ul>{notes.map((n, i) => <li key={n.id} className={`note ${n.color}`} style={{ animationDelay: `${i * 60}ms` }}>
      <span>{n.text}</span><button aria-label="Fatto, togli" onClick={() => void done(n.id)}>✓</button></li>)}</ul>}
  </aside>;
}
