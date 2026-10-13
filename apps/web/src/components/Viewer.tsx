import { useEffect, useState } from "react";
import { downloadFile } from "../api";

export type ViewItem = { title: string; url: string; kind: "image" | "pdf" | "doc" | "sheet" | "slides" | "text" | string };

const authHeaders = (): Record<string, string> => {
  const t = localStorage.getItem("nodo_token");
  return t ? { Authorization: `Bearer ${t}` } : {};
};

/** Big on-screen view of what NODO made: images and PDFs as they are, Word/Excel/slides as readable text.
 *  Opens when NODO shows something («fammi vedere…»), closes with ×, Esc, or by voice («chiudi»). */
export function Viewer({ item, onClose }: { item: ViewItem | null; onClose: () => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSrc(null); setText(null); setError(null);
    if (!item) return;
    let url: string | null = null, gone = false;
    const name = item.url.split("/").pop() || "";
    if (item.kind === "image" || item.kind === "pdf") {
      fetch(item.url, { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error(`il file non si apre (${r.status})`);
        return r.blob();
      }).then((b) => { if (!gone) { url = URL.createObjectURL(b); setSrc(url); } })
        .catch((e) => !gone && setError((e as Error).message));
    } else {
      fetch(`/api/v1/files/${name}/preview`, { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error(`anteprima non disponibile (${r.status})`);
        return r.json();
      }).then((d: { text: string }) => !gone && setText(d.text || "(documento vuoto)"))
        .catch((e) => !gone && setError((e as Error).message));
    }
    return () => { gone = true; if (url) URL.revokeObjectURL(url); };
  }, [item]);

  useEffect(() => {
    if (!item) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [item, onClose]);

  if (!item) return null;
  const rows = item.kind === "sheet" && text ? text.split("\n").filter(Boolean) : null;
  return <div className="viewer-backdrop" onClick={onClose}>
    <section className={`viewer ${item.kind}`} role="dialog" aria-label={item.title} onClick={(e) => e.stopPropagation()}>
      <header>
        <div><span className="eyebrow">SUL TUO SCHERMO</span><h3>{item.title}</h3></div>
        <div className="viewer-actions">
          <button onClick={() => downloadFile(item.url).catch((e) => setError((e as Error).message))}>Scarica ↓</button>
          <button className="close" aria-label="Chiudi" onClick={onClose}>×</button>
        </div>
      </header>
      <div className="viewer-body">
        {error && <p className="viewer-error">{error}</p>}
        {!error && !src && text === null && <div className="viewer-loading"><i /><i /><i /></div>}
        {src && item.kind === "image" && <img src={src} alt={item.title} />}
        {src && item.kind === "pdf" && <iframe src={src} title={item.title} />}
        {rows && <table><tbody>{rows.map((r, i) => r.startsWith("## ")
          ? <tr key={i} className="sheet-name"><td colSpan={99}>{r.slice(3)}</td></tr>
          : <tr key={i}>{r.split(" | ").map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table>}
        {text !== null && !rows && <article className="viewer-text">{text}</article>}
      </div>
    </section>
  </div>;
}
