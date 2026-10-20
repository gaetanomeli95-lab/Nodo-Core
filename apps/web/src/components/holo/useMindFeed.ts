import { useEffect, useRef, useState } from "react";
import { get } from "../../api";
import { perf } from "../../motion";

/** What Maria's mind is really doing, for the bridge (Bridge.tsx): her memory counters, the things she just
 *  remembered because a name was heard or said, and the events NODO really produced (/api/v1/activity), even
 *  while nobody talks. `pulse` changes whenever something real happens: the sphere flares with it. */

type GNode = { id: string; label: string; kind: string; weight: number; words: string[] };
type Graph = { nodes: GNode[]; edges: [string, string][]; stats: Record<string, number> };
export type Thought = { t: string; text: string; kind: "sento" | "ricordo" | "fatto" | "lavoro" };

function norm(t: string) { return ` ${t.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9' ]+/g, " ")} `; }
const hhmm = () => new Date().toTimeString().slice(0, 5);

export function useMindFeed({ heard, said, activity, revision }: {
  heard?: string; said?: string; activity?: string | null; revision?: number;
}) {
  const [stats, setStats] = useState<Record<string, number>>({});
  const [log, setLog] = useState<Thought[]>([]);
  const [lit, setLit] = useState<string[]>([]);
  const [pulse, setPulse] = useState(0);
  const nodes = useRef<GNode[]>([]);
  const seen = useRef<Set<string>>(new Set());

  const note = (text: string, kind: Thought["kind"]) =>
    setLog((l) => [{ t: hhmm(), text, kind }, ...l.filter((x) => x.text !== text)].slice(0, 5));

  useEffect(() => {                                   // the memory: at start, then when it may have changed
    let alive = true;
    get<Graph>("/api/v1/mind/graph").then((g) => {
      if (!alive || !g?.nodes) return;
      // a word lights a memory only if it is distinctive: «materassi» is everywhere, «Marcello» is one person
      const df = new Map<string, number>();
      for (const n of g.nodes) for (const w of new Set(n.words || [])) df.set(w, (df.get(w) || 0) + 1);
      nodes.current = g.nodes.filter((n) => n.kind !== "hub" && n.kind !== "core")
        .map((n) => ({ ...n, words: (n.words || []).filter((w, k) => (k === 0 && w.includes(" ")) || (df.get(w) || 0) <= 3) }));
      setStats(g.stats || {});
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [revision]);

  const touch = (text: string | undefined, kind: "sento" | "ricordo") => {
    if (!text || !nodes.current.length) return;
    const n = norm(text);
    const rank: Record<string, number> = { person: 0, customer: 1, rule: 2, note: 3, product: 4 };
    const found = nodes.current.filter((p) => p.words.some((w) => w.length >= 4 && n.includes(` ${norm(w).trim()}`)))
      .sort((a, b) => (rank[a.kind] ?? 9) - (rank[b.kind] ?? 9)).slice(0, 4).map((p) => p.label);
    if (!found.length) return;
    setLit(found);
    setPulse((x) => x + 1);
    note(`${kind === "sento" ? "Sento" : "Ricordo"}: ${found.slice(0, 3).join(", ")}`, kind);
  };
  useEffect(() => touch(heard, "sento"), [heard]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => touch(said, "ricordo"), [said]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (activity) { note(activity, "lavoro"); setPulse((x) => x + 1); } }, [activity]);
  useEffect(() => {                                   // lit memories go dark after a while
    if (!lit.length) return;
    const t = setTimeout(() => setLit([]), 12000);
    return () => clearTimeout(t);
  }, [lit]);

  useEffect(() => {                                   // what NODO really does, even while nobody talks
    let alive = true, since: string | undefined, timer = 0;
    const tick = async () => {
      if (!document.hidden) {
        try {
          const r = await get<{ events: { id: string; kind: string; what: string }[]; now: string }>(
            "/api/v1/activity" + (since ? `?since=${encodeURIComponent(since)}` : ""));
          if (alive && r?.events) {
            since = r.now;
            let fresh = false;
            for (const ev of [...r.events].reverse()) {
              if (seen.current.has(ev.id)) continue;
              seen.current.add(ev.id);
              if (seen.current.size > 300) seen.current.clear();
              // raw event names («voice.session.started») say nothing to Gaetano: only real sentences go up
              if (ev.kind !== "voice" && !/^[\w-]+(\.[\w-]+)+$/.test(ev.what.trim())) { note(ev.what, "fatto"); fresh = true; }
            }
            if (fresh) setPulse((x) => x + 1);
          }
        } catch { /* the bridge keeps going without the feed */ }
      }
      if (alive) timer = window.setTimeout(tick, perf.low ? 12000 : 6000);
    };
    void tick();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  return { stats, log, lit, pulse };
}
