import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { get } from "../../api";
import type { ViewItem } from "../Viewer";

/** Watch NODO work. «Voglio vedere proprio quando scrivi i codici» (18/10): while a site or a long job is being
 *  made, this window shows the file growing line by line, the seconds passing and the true state; when it is
 *  ready it opens by itself. If nothing is running, it shows nothing — no fake progress, ever. */

type Job = {
  id: string; title: string; kind: string; status: "in corso" | "finito" | "fallito"; step: string; note: string;
  url: string | null; view: string | null; file: string | null; bytes: number; lines: number; tail: string;
  seconds: number;
};

const fmt = (s: number) => (s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`);

export function LiveWork({ onOpen }: { onOpen: (item: ViewItem) => void }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const opened = useRef<Set<string>>(new Set());
  const code = useRef<HTMLPreElement>(null);
  const running = jobs.some((j) => j.status === "in corso");

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      if (!document.hidden) {
        try {
          const r = await get<{ jobs: Job[] }>("/api/v1/work/live");
          if (!alive) return;
          setJobs(r.jobs || []);
          for (const j of r.jobs || []) {               // ready: it opens by itself, once
            if (j.status === "finito" && j.url && !opened.current.has(j.id) && j.seconds < 3600) {
              opened.current.add(j.id);
              if (j.view === "web" || j.view === "pdf" || j.view === "image") {
                onOpen({ title: j.title, url: j.url, kind: j.view });
                setHidden((h) => new Set(h).add(j.id));   // the result is on screen: the code window steps aside
              }
            }
          }
        } catch { /* the panel keeps going without it */ }
      }
      if (alive) timer = window.setTimeout(tick, document.hidden ? 15000 : runningRef.current ? 1500 : 8000);
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const runningRef = useRef(false);
  runningRef.current = running;

  useEffect(() => { if (code.current) code.current.scrollTop = code.current.scrollHeight; }, [jobs]);

  // jobs finished long ago stay quiet; the current one (or the last result, for a while) is shown
  const shown = jobs.filter((j) => !hidden.has(j.id) && (j.status === "in corso" || j.seconds < 7200)).slice(0, 1);
  if (!shown.length) return null;
  const j = shown[0];
  const lines = j.tail.split("\n").slice(-26).join("\n");
  // on <body>: a parent with a transform (the holo stage) would otherwise trap the fixed window in the middle
  return createPortal(<aside className={`live-work ${j.status === "in corso" ? "running" : j.status === "finito" ? "done" : "failed"}`}
    aria-label="Lavoro in corso" role="status">
    <header>
      <span className="lw-dot" />
      <b>{j.title}</b>
      <span className="lw-state">{j.status === "in corso" ? j.step || "al lavoro" : j.status === "finito" ? "pronto" : "non riuscito"}</span>
      <span className="lw-time">{fmt(j.seconds)}</span>
      <button aria-label="Nascondi" onClick={() => setHidden((h) => new Set(h).add(j.id))}>×</button>
    </header>
    {j.tail ? <pre ref={code} className="lw-code">{lines}{j.status === "in corso" && <i className="lw-caret" />}</pre>
      : <p className="lw-wait">{j.status === "in corso" ? "Sto preparando il lavoro: appena inizio a scrivere lo vedi qui." : j.note}</p>}
    <footer>
      <span>{j.file ? `${j.file} · ${j.lines} righe · ${(j.bytes / 1024).toFixed(1)} KB` : ""}</span>
      {j.status === "finito" && j.url && <button onClick={() => onOpen({ title: j.title, url: j.url!, kind: j.view || "text" })}>Apri</button>}
      {j.status === "fallito" && <span className="lw-note">{j.note}</span>}
    </footer>
  </aside>, document.body);
}
