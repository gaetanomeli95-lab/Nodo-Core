import { useEffect, useState } from "react";
import { downloadFile } from "../api";

export type ViewItem = { title: string; url: string; kind: "image" | "pdf" | "doc" | "sheet" | "slides" | "text" | string };

const authHeaders = (): Record<string, string> => {
  const t = localStorage.getItem("nodo_token");
  return t ? { Authorization: `Bearer ${t}` } : {};
};

/** Big on-screen view: images, videos (with their sound), music, PDFs, YouTube, Word/Excel/slides as text — what
 *  NODO made or what is on Gaetano's PC. It lands with a flash and a ring of light («baam»), like a hologram.
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
    const direct = /^https?:/.test(item.url) || item.url.startsWith("/api/v1/view/");   // a link, or a file of the PC
    if (["video", "audio", "youtube", "web"].includes(item.kind) || (direct && ["image", "pdf"].includes(item.kind))) {
      setSrc(item.url);                                   // streamed as it is (a video can be big: no copy in memory)
    } else if (item.kind === "image" || item.kind === "pdf") {
      fetch(item.url, { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error(`il file non si apre (${r.status})`);
        return r.blob();
      }).then((b) => { if (!gone) { url = URL.createObjectURL(b); setSrc(url); } })
        .catch((e) => !gone && setError((e as Error).message));
    } else {
      const preview = item.url.startsWith("/api/v1/view/") ? `${item.url}/preview` : `/api/v1/files/${name}/preview`;
      fetch(preview, { headers: authHeaders() }).then((r) => {
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
  const media = ["video", "youtube", "image"].includes(item.kind);
  return <div className={`viewer-backdrop baam${media ? " cinema" : ""}`} onClick={onClose}>
    <i className="baam-flash" /><i className="baam-ring" />
    <section className={`viewer ${item.kind}`} role="dialog" aria-label={item.title} onClick={(e) => e.stopPropagation()}>
      <i className="viewer-corner tl" /><i className="viewer-corner tr" /><i className="viewer-corner bl" /><i className="viewer-corner br" />
      <header>
        <div><span className="eyebrow">SUL TUO SCHERMO</span><h3>{item.title}</h3></div>
        <div className="viewer-actions">
          {!["youtube", "web"].includes(item.kind) && !item.url.startsWith("/api/v1/view/") &&
            <button onClick={() => downloadFile(item.url).catch((e) => setError((e as Error).message))}>Scarica ↓</button>}
          {["web", "youtube"].includes(item.kind) && <a className="viewer-open" href={item.url.replace("youtube-nocookie.com/embed/", "youtube.com/watch?v=").replace(/\?autoplay.*$/, "")} target="_blank" rel="noreferrer">Apri fuori ↗</a>}
          <button className="close" aria-label="Chiudi" onClick={onClose}>×</button>
        </div>
      </header>
      <div className="viewer-body">
        {error && <p className="viewer-error">{error}</p>}
        {!error && !src && text === null && <div className="viewer-loading"><i /><i /><i /></div>}
        {src && item.kind === "image" && <img src={src} alt={item.title} />}
        {src && item.kind === "pdf" && <iframe src={src} title={item.title} />}
        {src && item.kind === "video" && <video src={src} controls autoPlay playsInline />}
        {src && item.kind === "audio" && <div className="viewer-audio"><i /><i /><i /><i /><i /><audio src={src} controls autoPlay /></div>}
        {src && item.kind === "youtube" && <iframe className="yt" src={src} title={item.title}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen />}
        {src && item.kind === "web" && <iframe src={src} title={item.title} sandbox="allow-scripts allow-same-origin allow-popups" />}
        {rows && <table><tbody>{rows.map((r, i) => r.startsWith("## ")
          ? <tr key={i} className="sheet-name"><td colSpan={99}>{r.slice(3)}</td></tr>
          : <tr key={i}>{r.split(" | ").map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table>}
        {text !== null && !rows && <article className="viewer-text">{text}</article>}
      </div>
    </section>
  </div>;
}
