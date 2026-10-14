import { useEffect, useState } from "react";
import { calm } from "../../motion";

const LINES = ["NODO CORE · avvio dei sistemi", "occhi · orecchie · voce · mani", "memoria e registro collegati",
  "turno di notte in ascolto", "pronta."];

/** A short boot sequence (once per browser session): NODO "wakes up" before the room appears. */
export function Boot({ name }: { name: string }) {
  const [step, setStep] = useState(0);
  const [gone, setGone] = useState(() => {
    try { return sessionStorage.getItem("nodo.booted") === "1" || calm; }
    catch { return false; }
  });
  useEffect(() => {
    if (gone) return;
    const t = setInterval(() => setStep((s) => s + 1), 300);
    const end = setTimeout(() => { setGone(true); try { sessionStorage.setItem("nodo.booted", "1"); } catch { /* ignore */ } }, 2000);
    return () => { clearInterval(t); clearTimeout(end); };
  }, [gone]);
  if (gone) return null;
  return <div className={`holo-boot ${step > LINES.length + 1 ? "out" : ""}`} onClick={() => setGone(true)}>
    <div className="boot-name">{name}</div>
    <ol>{LINES.slice(0, step).map((l, i) => <li key={i}><span>{String(i + 1).padStart(2, "0")}</span>{l}</li>)}</ol>
  </div>;
}
