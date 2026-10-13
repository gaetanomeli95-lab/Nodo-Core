import { useEffect, useState } from "react";

/** Live captions under the core: what you just said, and NODO's answer appearing word by word. */
export function Subtitles({ user, nodo, partial }: { user?: string; nodo?: string; partial?: string }) {
  const words = (nodo || "").split(/\s+/).filter(Boolean);
  const [shown, setShown] = useState(words.length);
  const [stale, setStale] = useState(false);
  useEffect(() => {                              // captions fade away after a quiet while
    setStale(false);
    const t = setTimeout(() => setStale(true), 25_000);
    return () => clearTimeout(t);
  }, [user, nodo, partial]);
  useEffect(() => {
    if (!nodo) return;
    setShown(0);
    const t = setInterval(() => setShown((n) => { if (n >= words.length) { clearInterval(t); return n; } return n + 1; }), 55);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodo]);
  if ((!user && !nodo && !partial) || (stale && !partial)) return null;
  const clip = (s: string, n: number) => (s.length > n ? "…" + s.slice(-n) : s);
  return <div className="holo-subs" aria-live="polite">
    {(partial || user) && <p className="you"><b>TU</b>{clip(partial || user || "", 160)}</p>}
    {nodo && <p className="nodo"><b>NODO</b>{clip(words.slice(0, shown).join(" "), 320)}<i className="caret" /></p>}
  </div>;
}
