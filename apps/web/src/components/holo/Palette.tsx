import { useEffect, useMemo, useRef, useState } from "react";

export type Action = { id: string; label: string; hint?: string; run: () => void };

/** Ctrl+K: everything NODO can do, one keystroke away. Unknown text is sent to NODO as a request. */
export function Palette({ actions, onAsk }: { actions: Action[]; onAsk: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); setQ(""); setSel(0); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", key);
    const ext = () => { setOpen(true); setQ(""); setSel(0); };
    window.addEventListener("nodo:palette", ext);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("nodo:palette", ext); };
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 30); }, [open]);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? actions.filter((a) => (a.label + " " + (a.hint || "")).toLowerCase().includes(t)) : actions;
  }, [q, actions]);
  if (!open) return null;
  const go = (i: number) => {
    setOpen(false);
    if (list[i]) list[i].run(); else if (q.trim()) onAsk(q.trim());
  };
  return <div className="viewer-backdrop holo-palette-wrap" onClick={() => setOpen(false)}>
    <div className="holo-palette" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Comandi">
      <input ref={input} value={q} placeholder="Cosa vuoi fare? (o scrivi a NODO e premi Invio)"
        onChange={(e) => { setQ(e.target.value); setSel(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, list.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
          if (e.key === "Enter") { e.preventDefault(); go(list.length ? sel : -1); }
        }} />
      <ul>{list.map((a, i) => <li key={a.id} className={i === sel ? "on" : ""} onMouseEnter={() => setSel(i)} onClick={() => go(i)}>
        <span>{a.label}</span>{a.hint && <small>{a.hint}</small>}</li>)}
        {!list.length && q.trim() && <li className="on" onClick={() => go(-1)}><span>Chiedi a NODO: «{q.trim()}»</span><small>Invio</small></li>}
      </ul>
      <footer><kbd>↑↓</kbd> scegli · <kbd>Invio</kbd> esegui · <kbd>Esc</kbd> chiudi</footer>
    </div>
  </div>;
}
