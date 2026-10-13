import { useEffect, useRef, useState } from "react";

/** NODO's eyes: the camera (or your screen) in a holographic frame. «Scatta e chiedi» hands one frame to NODO. */
export function Eyes({ source, onShot, onClose }: {
  source: "camera" | "screen" | null; onShot: (file: File, question: string) => void; onClose: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (!source) return;
    let alive = true;
    setError(null);
    const open = source === "camera"
      ? navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 }, facingMode: "environment" }, audio: false })
      : navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    open.then((s) => {
      if (!alive) { s.getTracks().forEach((t) => t.stop()); return; }
      stream.current = s;
      if (video.current) { video.current.srcObject = s; void video.current.play(); }
      s.getVideoTracks()[0]?.addEventListener("ended", onClose);
    }).catch(() => setError(source === "camera" ? "Non vedo la camera: consenti l'accesso dal lucchetto vicino all'indirizzo."
      : "Condivisione dello schermo annullata."));
    return () => { alive = false; stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);
  if (!source) return null;
  const shoot = () => {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d")!.drawImage(v, 0, 0);
    setFlash(true); setTimeout(() => setFlash(false), 350);
    c.toBlob((b) => {
      if (!b) return;
      onShot(new File([b], `${source === "camera" ? "camera" : "schermo"}-${Date.now()}.jpg`, { type: "image/jpeg" }), question);
      onClose();
    }, "image/jpeg", 0.88);
  };
  return <div className="viewer-backdrop holo-eyes-wrap" onClick={onClose}>
    <section className="holo-eyes" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Occhi di NODO">
      <header><span className="eyebrow">{source === "camera" ? "OCCHI · CAMERA" : "OCCHI · IL TUO SCHERMO"}</span>
        <button className="close" aria-label="Chiudi" onClick={onClose}>×</button></header>
      <div className="lens">
        <video ref={video} playsInline muted />
        <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
        <i className="scan" />
        {flash && <i className="flash" />}
        {error && <p className="viewer-error">{error}</p>}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); shoot(); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Cosa deve guardare? (es. leggi la bolla e segna le vendite)" />
        <button className="primary">Scatta e chiedi</button>
      </form>
    </section>
  </div>;
}
