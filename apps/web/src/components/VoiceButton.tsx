/**
 * Push-to-talk (V0.1). Hold the button (or Space) to speak, release to send.
 * STT: browser Web Speech API when the server reports `stt.location === "browser"` (free, on-device),
 * otherwise MediaRecorder audio is uploaded to /voice/transcribe. TTS mirrors the same choice.
 * Barge-in: pressing the button while NODO speaks cancels playback and notifies the voice session.
 */
import { useEffect, useRef, useState } from "react";
import { post, speak, transcribe, type VoiceConfig } from "../api";

export type VoiceState = "IDLE" | "LISTENING" | "UNDERSTANDING" | "THINKING" | "SPEAKING" | "ERROR";

type Props = {
  config: VoiceConfig | null;
  state: VoiceState;
  setState: (s: VoiceState) => void;
  onTranscript: (text: string) => void;
};

type Recognition = { lang: string; interimResults: boolean; continuous: boolean; start(): void; stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null; onerror: ((e: unknown) => void) | null; onend: (() => void) | null };

export function VoiceButton({ config, state, setState, onTranscript }: Props) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const recRef = useRef<Recognition | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const finalText = useRef("");

  useEffect(() => {
    post<{ id: string }>("/api/v1/voice/sessions", {}).then((s) => setSessionId(s.id)).catch(() => {});
  }, []);

  const setServerState = (s: VoiceState) =>
    sessionId && fetch(`/api/v1/voice/sessions/${sessionId}/state?state=${s}`, { method: "POST" }).catch(() => {});

  const start = async () => {
    if (state === "SPEAKING") stopSpeaking(sessionId); // barge-in
    if (state !== "IDLE" && state !== "SPEAKING") return;
    setState("LISTENING");
    setServerState("LISTENING");
    finalText.current = "";
    const useBrowser = config?.stt.location === "browser";
    const SR = (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition });
    const Ctor = SR.SpeechRecognition || SR.webkitSpeechRecognition;
    if (useBrowser && Ctor) {
      const rec = new Ctor();
      rec.lang = config?.language === "it" ? "it-IT" : config?.language || "it-IT";
      rec.interimResults = true;
      rec.continuous = true;
      rec.onresult = (e) => { finalText.current = Array.from(e.results).map((r) => r[0].transcript).join(" "); };
      rec.onerror = () => setState("ERROR");
      rec.onend = () => finish();
      recRef.current = rec;
      rec.start();
    } else if (!useBrowser && navigator.mediaDevices) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      chunks.current = [];
      mr.ondataavailable = (e) => chunks.current.push(e.data);
      mr.onstop = () => { stream.getTracks().forEach((t) => t.stop()); finish(); };
      mediaRef.current = mr;
      mr.start();
    } else {
      setState("ERROR");
    }
  };

  const stop = () => {
    if (recRef.current) { recRef.current.stop(); recRef.current = null; return; }
    if (mediaRef.current) { mediaRef.current.stop(); mediaRef.current = null; }
  };

  const finish = async () => {
    setState("UNDERSTANDING");
    try {
      const res = config?.stt.location === "browser"
        ? await transcribe({ text: finalText.current.trim(), sessionId: sessionId || undefined })
        : await transcribe({ blob: new Blob(chunks.current, { type: "audio/webm" }), sessionId: sessionId || undefined });
      if (res.text.trim()) onTranscript(res.text.trim()); else setState("IDLE");
    } catch { setState("ERROR"); setTimeout(() => setState("IDLE"), 1500); }
  };

  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.code === "Space" && (e.target as HTMLElement).tagName !== "TEXTAREA" && !e.repeat) { e.preventDefault(); start(); } };
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && (e.target as HTMLElement).tagName !== "TEXTAREA") stop(); };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  });

  return (
    <button className={`mic ${state.toLowerCase()}`} onMouseDown={start} onMouseUp={stop} onMouseLeave={stop}
      onTouchStart={start} onTouchEnd={stop} title="Tieni premuto per parlare (o barra spaziatrice)">
      <span className="mic-dot" />
      {state === "LISTENING" ? "Ti ascolto" : state === "SPEAKING" ? "Tocca per interrompere" : "Tieni premuto e parla"}
    </button>
  );
}

let currentAudio: HTMLAudioElement | null = null;

export async function speakText(text: string, config: VoiceConfig | null, onEnd: () => void) {
  if (config?.tts.location === "browser") {
    if (!("speechSynthesis" in window)) return onEnd();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = config.language === "it" ? "it-IT" : config.language;
    u.onend = onEnd; u.onerror = onEnd;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    return;
  }
  try {
    const { data } = await speak(text);
    currentAudio = new Audio(URL.createObjectURL(data));
    currentAudio.onended = onEnd; currentAudio.onerror = onEnd;
    await currentAudio.play();
  } catch { onEnd(); }
}

export function stopSpeaking(sessionId: string | null) {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  if (sessionId) fetch(`/api/v1/voice/sessions/${sessionId}/interrupt`, { method: "POST" }).catch(() => {});
}
