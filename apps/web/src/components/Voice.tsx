/**
 * Realtime voice (Phase 2): WebSocket session -> typed events -> the same Core as text.
 * Push-to-talk stays the reliable capture mechanism; interruption is a real protocol event.
 * Browser speech APIs do the heavy lifting in FREE mode (stt/tts location === "browser").
 */
import { useEffect, useRef, useState } from "react";
import { VoiceSocket, type VoiceConfig, type VoiceEvent } from "../api";

export type VoiceState =
  | "IDLE" | "CONNECTING" | "LISTENING" | "TRANSCRIBING" | "UNDERSTANDING" | "THINKING"
  | "ACTING" | "SPEAKING" | "INTERRUPTED" | "WAITING_FOR_APPROVAL" | "ERROR" | "CLOSED";

export type VoiceCallbacks = {
  onEvent: (e: VoiceEvent) => void;
  onUserText: (text: string) => void;
  onNodoStart: () => void;
  onNodoDelta: (delta: string) => void;
  onNodoDone: (meta: Record<string, unknown>) => void;
  onError: (msg: string) => void;
};

type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean;
  start(): void; stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }> }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  onend: (() => void) | null;
};

/** Sequential playback for server-side `tts.chunk` audio (one Blob per chunk, played in order). */
class ChunkPlayer {
  private queue: string[] = [];
  private playing = false;
  private current: HTMLAudioElement | null = null;

  push(b64: string, mime: string) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    this.queue.push(URL.createObjectURL(new Blob([bytes], { type: mime })));
    void this.pump();
  }
  private async pump() {
    if (this.playing) return;
    this.playing = true;
    for (const url = this.queue.shift(); url;) {
      this.current = new Audio(url);
      try { await this.current.play(); await new Promise((r) => { this.current!.onended = r; this.current!.onerror = r; }); }
      catch { /* audio playback may be blocked until a user gesture */ }
      URL.revokeObjectURL(url);
    }
    this.playing = false;
    this.current = null;
  }
  stop() {
    this.queue = [];
    this.current?.pause();
    this.current = null;
    this.playing = false;
  }
}

export function useVoiceSession(config: VoiceConfig | null, cb: VoiceCallbacks) {
  const [state, setState] = useState<VoiceState>("CONNECTING");
  const [connected, setConnected] = useState(false);
  const [partial, setPartial] = useState("");
  const sock = useRef<VoiceSocket | null>(null);
  const rec = useRef<Recognition | null>(null);
  const player = useRef(new ChunkPlayer());
  const held = useRef(false);
  const speechQueue = useRef<string[]>([]);
  const speaking = useRef(false);
  const finalBuf = useRef("");
  const streamingStarted = useRef(false);
  const media = useRef<{ rec: MediaRecorder; stream: MediaStream } | null>(null);
  const cbRef = useRef(cb);
  cbRef.current = cb;

  const browserTtsNext = () => {
    const text = speechQueue.current.shift();
    if (!text || !("speechSynthesis" in window)) { speaking.current = false; return; }
    speaking.current = true;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = config?.language === "it" ? "it-IT" : config?.language || "it-IT";
    u.onend = browserTtsNext; u.onerror = browserTtsNext;
    window.speechSynthesis.speak(u);
  };

  useEffect(() => {
    const s = new VoiceSocket(
      (e) => {
        const c = cbRef.current;
        c.onEvent(e);
        switch (e.type) {
          case "voice.session.state":
            setState(e.state as VoiceState);
            break;
          case "voice.session.started":
            setConnected(true);
            break;
          case "transcript.partial":
            setPartial(e.text as string);
            break;
          case "transcript.final":
            setPartial("");
            c.onUserText(e.text as string);
            break;
          case "nodo.thinking":
            c.onNodoStart();
            break;
          case "nodo.token": {
            if (!streamingStarted.current) { streamingStarted.current = true; c.onNodoStart(); }
            c.onNodoDelta(e.delta as string);
            break;
          }
          case "tts.speak":
            speechQueue.current.push(e.text as string);
            if (!speaking.current) browserTtsNext();
            break;
          case "tts.chunk":
            player.current.push(e.audio as string, e.mime as string);
            break;
          case "voice.interrupted":
            setPartial("");
            stopLocalAudio();
            break;
          case "nodo.response":
            streamingStarted.current = false;
            c.onNodoDone(e as unknown as Record<string, unknown>);
            break;
          case "voice.turn.done":
            streamingStarted.current = false;
            break;
          case "error":
            c.onError(e.message as string);
            break;
        }
      },
      () => setConnected(false),
    );
    s.send({ type: "session.start", language: config?.language || "it", client_stt: true });
    sock.current = s;
    return () => { s.close(); stopLocalAudio(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopLocalAudio = () => {
    speechQueue.current = [];
    speaking.current = false;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    player.current.stop();
  };

  const pressStart = () => {
    if (!connected) return;
    if (state === "SPEAKING" || state === "THINKING" || state === "ACTING") interrupt(); // barge-in
    if (state !== "IDLE" && state !== "LISTENING" && state !== "INTERRUPTED") return;
    held.current = true;
    finalBuf.current = "";
    const SR = (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition });
    const Ctor = SR.SpeechRecognition || SR.webkitSpeechRecognition;
    if (Ctor) {
      const r = new Ctor();
      r.lang = config?.language === "it" ? "it-IT" : config?.language || "it-IT";
      r.interimResults = true;
      r.continuous = true;
      r.onresult = (e) => {
        let interim = "", fin = "";
        for (const res of Array.from(e.results)) {
          if (res.isFinal) fin += res[0].transcript;
          else interim += res[0].transcript;
        }
        finalBuf.current += fin;
        const live = (finalBuf.current + " " + interim).trim();
        if (live) sock.current?.send({ type: "transcript.partial", text: live });
      };
      r.onerror = () => { };
      r.onend = () => { if (held.current) commit(); };
      rec.current = r;
      r.start();
    } else {
      // no browser STT: capture audio and let the server transcribe on release
      void startAudioCapture();
    }
  };

  const startAudioCapture = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mr.ondataavailable = (e) => {
        e.data.arrayBuffer().then((buf) => {
          const bytes = new Uint8Array(buf);
          let bin = "";
          for (const b of bytes) bin += String.fromCharCode(b);
          sock.current?.send({ type: "audio.chunk", data: btoa(bin), mime: "audio/webm" });
        });
      };
      mr.start(250); // chunk every 250ms
      media.current = { rec: mr, stream };
    } catch { cbRef.current.onError("microfono non disponibile"); }
  };

  const stopAudioCapture = () => {
    if (!media.current) return;
    media.current.rec.stop();
    media.current.stream.getTracks().forEach((t) => t.stop());
    media.current = null;
    sock.current?.send({ type: "speech.end" });
  };

  const commit = () => {
    const text = finalBuf.current.trim();
    if (text) sock.current?.send({ type: "transcript.final", text });
    else sock.current?.send({ type: "speech.end" });
  };

  const pressStop = () => {
    if (!held.current) return;
    held.current = false;
    if (rec.current) { rec.current.stop(); return; } // onend -> commit
    stopAudioCapture();
  };

  const interrupt = (reason = "user") => {
    stopLocalAudio();
    sock.current?.send({ type: "interrupt", reason });
  };

  const answer = (approvalId: string, decision: "approve" | "reject") =>
    sock.current?.send({ type: "approval.answer", approval_id: approvalId, decision });

  // push-to-talk on Space
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && (e.target as HTMLElement).tagName !== "TEXTAREA" && !e.repeat) {
        e.preventDefault(); pressStart();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && (e.target as HTMLElement).tagName !== "TEXTAREA") pressStop();
    };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  });

  return { state, connected, partial, pressStart, pressStop, interrupt, answer };
}

export function VoiceDock({ voice, onInterrupt }: {
  voice: ReturnType<typeof useVoiceSession>;
  onInterrupt?: () => void;
}) {
  const { state, connected, partial, pressStart, pressStop } = voice;
  const label = !connected ? "voce offline"
    : state === "LISTENING" ? "Ti ascolto"
    : state === "TRANSCRIBING" ? "Trascrivo"
    : state === "UNDERSTANDING" ? "Capisco"
    : state === "THINKING" ? "Sto pensando"
    : state === "ACTING" ? "Sto lavorando"
    : state === "SPEAKING" ? "Tocca per interrompere"
    : state === "INTERRUPTED" ? "Interrotto"
    : state === "WAITING_FOR_APPROVAL" ? "Serve conferma"
    : state === "ERROR" ? "Errore voce"
    : "Tieni premuto e parla";
  return (
    <div className="voice-dock">
      <button
        type="button"
        className={`mic ${state.toLowerCase()}`}
        onMouseDown={pressStart} onMouseUp={pressStop} onMouseLeave={pressStop}
        onTouchStart={(e) => { e.preventDefault(); pressStart(); }}
        onTouchEnd={pressStop}
        title="Tieni premuto per parlare (o barra spaziatrice). Mentre NODO parla, premi per interrompere."
        disabled={!connected}
      >
        <span className="mic-dot" />{label}
      </button>
      {(state === "SPEAKING" || state === "THINKING" || state === "ACTING") && (
        <button type="button" className="ghost" onClick={() => { voice.interrupt(); onInterrupt?.(); }}>Ferma</button>
      )}
      {partial && <span className="partial">{partial}</span>}
    </div>
  );
}
