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
  private finishCurrent: (() => void) | null = null;
  private generation = 0;

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
    const generation = this.generation;
    try {
      // Each URL is played exactly once; an interrupt invalidates this generation.
      while (this.queue.length && generation === this.generation) {
        const url = this.queue.shift()!;
        const audio = new Audio(url);
        this.current = audio;
        try {
          await audio.play();
          if (generation !== this.generation) continue;
          await new Promise<void>((resolve) => {
            this.finishCurrent = resolve;
            audio.onended = () => resolve();
            audio.onerror = () => resolve();
          });
        } catch {
          // Autoplay can be blocked before the first user gesture.
        } finally {
          audio.pause();
          audio.onended = null;
          audio.onerror = null;
          if (this.current === audio) this.current = null;
          this.finishCurrent = null;
          URL.revokeObjectURL(url);
        }
      }
    } finally {
      this.playing = false;
      if (this.queue.length) void this.pump();
    }
  }

  stop() {
    this.generation += 1;
    for (const url of this.queue) URL.revokeObjectURL(url);
    this.queue = [];
    this.current?.pause();
    this.finishCurrent?.(); // release a pending onended promise on barge-in
  }
}

/** Streaming PCM playback for Gemini Live audio (`audio/pcm;rate=24000`): chunks are scheduled
 *  back-to-back on an AudioContext so speech starts with the first chunk and has no gaps. */
class PcmPlayer {
  private ctx: AudioContext | null = null;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();

  push(b64: string, mime: string) {
    const m = /rate=(\d+)/.exec(mime || "");
    const rate = m ? parseInt(m[1], 10) : 24000;
    const bin = atob(b64);
    const raw = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) raw[i] = bin.charCodeAt(i);
    const i16 = new Int16Array(raw.buffer, 0, Math.floor(raw.length / 2));
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
    if (!f32.length) return;
    if (!this.ctx) this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") void this.ctx.resume();
    const buf = this.ctx.createBuffer(1, f32.length, rate);
    buf.copyToChannel(f32, 0);
    const s = this.ctx.createBufferSource();
    s.buffer = buf;
    s.connect(this.ctx.destination);
    const t = Math.max(this.ctx.currentTime + 0.02, this.next);
    s.start(t);
    this.next = t + buf.duration;
    this.sources.add(s);
    s.onended = () => this.sources.delete(s);
  }

  stop() {
    for (const s of this.sources) { try { s.stop(); } catch { /* already ended */ } }
    this.sources.clear();
    this.next = this.ctx ? this.ctx.currentTime : 0;
  }
}

/** Float32 mic samples -> 16-bit PCM at outRate (nearest-sample downsample: enough for speech). */
function toPcm16(input: Float32Array, inRate: number, outRate: number): Uint8Array {
  const ratio = inRate / outRate;
  const n = Math.floor(input.length / ratio);
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
    out[i] = s * 0x7fff;
  }
  return new Uint8Array(out.buffer);
}

function toB64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function useVoiceSession(config: VoiceConfig | null, cb: VoiceCallbacks) {
  const [state, setState] = useState<VoiceState>("CONNECTING");
  const [connected, setConnected] = useState(false);
  const [partial, setPartial] = useState("");
  const [engine, setEngine] = useState<"live" | "browser">("browser");   // live = Gemini Live (real-time voice)
  const [liveOn, setLiveOn] = useState(false);                            // live mode: conversation mic open
  const [handsFree, setHandsFree] = useState(false);                      // explicit opt-in; browser half-duplex
  const sock = useRef<VoiceSocket | null>(null);
  const rec = useRef<Recognition | null>(null);
  const handsFreeRef = useRef(false);
  const connectedRef = useRef(false);
  const engineRef = useRef<"live" | "browser">("browser");
  const awaitingTurn = useRef(false);
  const silenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastInterim = useRef("");
  const handsFreeSupported = typeof window !== "undefined" &&
    !!((window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown })
      .SpeechRecognition || (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition)
    && (config?.tts.location === "browser" || config === null);
  const player = useRef(new ChunkPlayer());
  const held = useRef(false);
  const toggled = useRef(false);
  const listening = useRef(false);   // browser speech recognition running for this press     // started with a click: listening until the next click
  const pressedAt = useRef(0);
  const speechQueue = useRef<string[]>([]);
  const speaking = useRef(false);
  const finalBuf = useRef("");
  const streamingStarted = useRef(false);
  const media = useRef<{ rec: MediaRecorder; stream: MediaStream } | null>(null);
  const pcm = useRef(new PcmPlayer());
  const liveMic = useRef<{ ctx: AudioContext; stream: MediaStream } | null>(null);
  const cbRef = useRef(cb);
  cbRef.current = cb;

  const ttsWarned = useRef(false);
  const ttsFail = (msg: string) => {
    if (ttsWarned.current) return;              // one clear notice, not one per sentence
    ttsWarned.current = true;
    cbRef.current.onError(msg);
  };
  const browserTtsNext = () => {
    const text = speechQueue.current.shift();
    if (!text) { speaking.current = false; maybeResumeHandsFree(); return; }
    if (!("speechSynthesis" in window)) {
      speaking.current = false;
      ttsFail("questo browser non può leggere le risposte ad alta voce: usa Chrome o Edge");
      stopHandsFree();
      return;
    }
    speaking.current = true;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = config?.language === "it" ? "it-IT" : config?.language || "it-IT";
    const voices = window.speechSynthesis.getVoices().filter((v) => v.lang?.toLowerCase().startsWith("it"));
    // "natural"/online voices sound human; Google and Microsoft both ship good Italian ones
    const best = voices.find((v) => /natural|online|neural/i.test(v.name))
      || voices.find((v) => /google/i.test(v.name)) || voices[0];
    if (best) u.voice = best;
    u.onend = browserTtsNext;
    u.onerror = (ev) => {
      const code = (ev as SpeechSynthesisErrorEvent).error;
      if (code && code !== "interrupted" && code !== "canceled")
        ttsFail(`non riesco a leggere la risposta ad alta voce (${code}): controlla la voce italiana in
          Impostazioni di Windows → Ora e lingua → Voce`);
      browserTtsNext();
    };
    window.speechSynthesis.speak(u);
  };

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    const connect = () => {
      const s = new VoiceSocket(
        (e) => {
          const c = cbRef.current;
          c.onEvent(e);
          switch (e.type) {
            case "voice.session.state":
              setState(e.state as VoiceState);
              break;
            case "voice.session.started":
              attempt = 0;
              if ("speechSynthesis" in window) {
                window.speechSynthesis.getVoices();   // Chrome loads voices lazily: warm them up
                window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
              }
              engineRef.current = e.engine === "live" ? "live" : "browser";
              connectedRef.current = true;
              setEngine(engineRef.current);
              setConnected(true);
              setState(((e.state as VoiceState) || "IDLE"));   // ready: the button works from here
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
            case "voice.engine":       // live session died mid-conversation: browser fallback is opt-in
              engineRef.current = e.engine === "live" ? "live" : "browser";
              setEngine(engineRef.current);
              if (e.engine === "live") stopHandsFree();
              if (e.engine !== "live") {
                stopLiveMic();
                stopLocalAudio();  // never play buffered Live speech after the provider has fallen back
                setLiveOn(false);
              }
              break;
            case "tts.chunk":
              if ((e.mime as string || "").startsWith("audio/pcm")) pcm.current.push(e.audio as string, e.mime as string);
              else player.current.push(e.audio as string, e.mime as string);
              break;
            case "voice.interrupted":
              setPartial("");
              stopLocalAudio();
              awaitingTurn.current = false;
              maybeResumeHandsFree();
              break;
            case "nodo.response":
              streamingStarted.current = false;
              c.onNodoDone(e as unknown as Record<string, unknown>);
              break;
            case "voice.turn.done":
              streamingStarted.current = false;
              awaitingTurn.current = false;
              maybeResumeHandsFree(); // wait for the actual speechSynthesis queue, not just server completion
              break;
            case "error":
              c.onError(e.message as string);
              break;
          }
        },
        () => {
          // NODO restarted or the network dropped: try again (2s, 4s, ... up to 15s)
          connectedRef.current = false;
          stopHandsFree(); // a reconnect must never silently re-enable microphone capture
          setConnected(false);
          setState("CONNECTING");
          if (closed) return;
          attempt += 1;
          retry = setTimeout(connect, Math.min(15000, 2000 * attempt));
        },
      );
      let conversation: string | undefined;
      try { conversation = localStorage.getItem("nodo.conversation") || undefined; } catch { /* private mode */ }
      // same conversation as the text chat: the brain remembers what you wrote and what you said
      s.send({ type: "session.start", language: config?.language || "it", client_stt: true, conversation_id: conversation });
      sock.current = s;
    };
    connect();
    return () => { closed = true; if (retry) clearTimeout(retry); stopHandsFree(); sock.current?.close(); stopLocalAudio(); stopLiveMic(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopLocalAudio = () => {
    speechQueue.current = [];
    speaking.current = false;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    player.current.stop();
    pcm.current.stop();
  };

  // ---------------------------------------------------------- Gemini Live: mic streaming
  const startLiveMic = async (): Promise<boolean> => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      const ctx = new AudioContext();
      await ctx.audioWorklet.addModule("/pcm-worklet.js");
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "pcm-worklet");
      const srcRate = ctx.sampleRate;
      node.port.onmessage = (ev: MessageEvent) => {
        const pcm16 = toPcm16(ev.data as Float32Array, srcRate, 16000);
        if (pcm16.length) sock.current?.send({ type: "audio.chunk", data: toB64(pcm16), mime: "audio/pcm;rate=16000" });
      };
      // The worklet must stay running without monitoring the microphone on the speakers.
      // A zero-gain destination keeps the graph alive and avoids acoustic feedback.
      const silent = ctx.createGain();
      silent.gain.value = 0;
      src.connect(node);
      node.connect(silent);
      silent.connect(ctx.destination);
      liveMic.current = { ctx, stream };
      return true;
    } catch {
      cbRef.current.onError("microfono non disponibile: consenti l'accesso dal lucchetto vicino all'indirizzo e riprova");
      return false;
    }
  };

  const stopLiveMic = () => {
    if (!liveMic.current) return;
    liveMic.current.stream.getTracks().forEach((t) => t.stop());
    void liveMic.current.ctx.close();
    liveMic.current = null;
  };

  /** Live mode: one click opens the conversation (the mic streams, Gemini hears pauses and
   *  interruptions by itself); while NODO speaks a click interrupts it; another click hangs up. */
  const liveButton = async () => {
    if (!connected) return;
    if (liveOn && (state === "SPEAKING" || state === "THINKING" || state === "ACTING")) { interrupt(); return; }
    if (liveOn) {
      stopLiveMic();
      setLiveOn(false);
      sock.current?.send({ type: "speech.end" });
      setState("IDLE");
      return;
    }
    if (await startLiveMic()) { setLiveOn(true); setState("LISTENING"); }
  };

  // ---------------------------------------------------------- Browser conversation (explicit half-duplex)
  const clearSilence = () => {
    if (silenceTimer.current) clearTimeout(silenceTimer.current);
    silenceTimer.current = null;
  };
  const clearResume = () => {
    if (resumeTimer.current) clearTimeout(resumeTimer.current);
    resumeTimer.current = null;
  };

  const stopHandsFree = () => {
    handsFreeRef.current = false;
    setHandsFree(false);
    awaitingTurn.current = false;
    clearSilence();
    clearResume();
    if (rec.current) {
      const active = rec.current;
      rec.current = null; // ignore delayed onend after explicit microphone shutdown
      active.onend = null;
      active.onresult = null;
      active.onerror = null;
      try { active.stop(); } catch { /* already stopped */ }
    }
    listening.current = false;
    held.current = toggled.current = false;
    finalBuf.current = lastInterim.current = "";
    setPartial("");
  };

  const maybeResumeHandsFree = () => {
    if (!handsFreeRef.current || !connectedRef.current || engineRef.current !== "browser" ||
        awaitingTurn.current || speaking.current || speechQueue.current.length ||
        listening.current || rec.current || resumeTimer.current) return;
    // Browsers can end a recognition session on long silence. Retry is allowed only while
    // the user has explicitly kept hands-free enabled. No microphone stays open on disconnect.
    resumeTimer.current = setTimeout(() => {
      resumeTimer.current = null;
      if (handsFreeRef.current && connectedRef.current && !awaitingTurn.current &&
          !speaking.current && !rec.current) startBrowserRecognition(true);
    }, 350);
  };

  const startBrowserRecognition = (automatic: boolean) => {
    const SR = window as unknown as {
      SpeechRecognition?: new () => Recognition;
      webkitSpeechRecognition?: new () => Recognition;
    };
    const Ctor = SR.SpeechRecognition || SR.webkitSpeechRecognition;
    if (!Ctor) {
      if (automatic) stopHandsFree();
      cbRef.current.onError("Ascolto libero richiede il riconoscimento vocale di Chrome o Edge.");
      return;
    }
    clearSilence();
    clearResume();
    finalBuf.current = "";
    lastInterim.current = "";
    const r = new Ctor();
    r.lang = config?.language === "it" ? "it-IT" : config?.language || "it-IT";
    r.interimResults = true;
    r.continuous = true;
    r.onresult = (e) => {
      // Chrome repeats previously finalized results on later events. Reconstruct each
      // snapshot rather than appending the same transcript multiple times.
      const finalParts: string[] = [], interimParts: string[] = [];
      for (const result of Array.from(e.results)) {
        const value = result[0]?.transcript?.trim();
        if (!value) continue;
        if (result.isFinal) finalParts.push(value);
        else interimParts.push(value);
      }
      finalBuf.current = finalParts.join(" ");
      lastInterim.current = interimParts.join(" ");
      const partial = [finalBuf.current, lastInterim.current].filter(Boolean).join(" ").trim();
      if (partial) sock.current?.send({ type: "transcript.partial", text: partial });
      if (automatic && partial) {
        clearSilence();
        // A short pause after final speech closes the turn, but a still-unfinalized
        // phrase gets longer to avoid cutting off the user mid-sentence.
        silenceTimer.current = setTimeout(() => {
          if (rec.current === r && listening.current) {
            try { r.stop(); } catch { /* already closing */ }
          }
        }, interimParts.length ? 1700 : 1100);
      }
    };
    r.onerror = (ev: unknown) => {
      if (rec.current !== r) return;
      const why = (ev as { error?: string } | null)?.error;
      if (why === "no-speech" || why === "aborted") return;
      listening.current = false;
      clearSilence();
      if (automatic) stopHandsFree();
      else { held.current = false; toggled.current = false; }
      setState("IDLE");
      cbRef.current.onError(why === "not-allowed" || why === "service-not-allowed"
        ? "Microfono non autorizzato: consenti l'accesso nel browser."
        : why === "network" ? "Riconoscimento vocale del browser non raggiungibile: prova Chrome o Edge con internet."
        : why === "audio-capture" ? "Nessun microfono disponibile in Windows."
        : `Riconoscimento vocale interrotto (${why ?? "errore sconosciuto"}).`);
    };
    r.onend = () => {
      if (rec.current !== r) return;
      rec.current = null;
      listening.current = false;
      clearSilence();
      const text = (finalBuf.current || (automatic ? lastInterim.current : "")).trim();
      if (automatic) {
        if (!handsFreeRef.current) return;
        if (!text) { maybeResumeHandsFree(); return; }
        awaitingTurn.current = true;
        setState("UNDERSTANDING");
        sock.current?.send({ type: "transcript.final", text });
      } else {
        commit();
      }
    };
    rec.current = r;
    listening.current = true;
    setState("LISTENING");
    try { r.start(); } catch {
      if (rec.current === r) rec.current = null;
      listening.current = false;
      setState("IDLE");
      if (automatic) stopHandsFree();
      cbRef.current.onError("Riconoscimento vocale non disponibile: riattiva il microfono dal browser.");
    }
  };

  const toggleHandsFree = () => {
    if (handsFreeRef.current) {
      stopHandsFree();
      setState("IDLE");
      return;
    }
    if (!connectedRef.current || engineRef.current !== "browser" || !handsFreeSupported) return;
    if (speaking.current || awaitingTurn.current || listening.current) {
      cbRef.current.onError("Termina la risposta o la registrazione prima di attivare l'ascolto libero.");
      return;
    }
    handsFreeRef.current = true;
    setHandsFree(true);
    startBrowserRecognition(true);
  };

  const pressStart = () => {
    if (!connectedRef.current) return;
    if (engineRef.current === "live") { void liveButton(); return; }
    if (handsFreeRef.current) { toggleHandsFree(); return; }
    if (toggled.current) { toggled.current = false; finishListening(); return; }
    if (state === "SPEAKING" || state === "THINKING" || state === "ACTING") interrupt();
    if (state !== "IDLE" && state !== "LISTENING" && state !== "INTERRUPTED" && state !== "CONNECTING"
        && state !== "ERROR") return;
    held.current = true;
    pressedAt.current = Date.now();
    const SR = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
    if (SR.SpeechRecognition || SR.webkitSpeechRecognition) startBrowserRecognition(false);
    else { setState("LISTENING"); void startAudioCapture(); }
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
    toggled.current = false;
    held.current = false;
    const text = finalBuf.current.trim();
    if (text) sock.current?.send({ type: "transcript.final", text });
    else sock.current?.send({ type: "speech.end" });
  };

  // hold to talk, or click to start and click again to send
  const finishListening = () => {
    held.current = false;
    if (rec.current) { rec.current.stop(); return; } // onend -> commit
    stopAudioCapture();
  };

  const pressStop = () => {
    if (engine === "live") return;   // live conversation toggles on click, not on press-and-hold
    if (!held.current || toggled.current) return;
    if (Date.now() - pressedAt.current < 400) { toggled.current = true; return; }  // a click: keep listening
    finishListening();
  };

  const interrupt = (reason = "user") => {
    stopLocalAudio();
    sock.current?.send({ type: "interrupt", reason });
    awaitingTurn.current = false;
    maybeResumeHandsFree();
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

  // Text and voice share the same conversation ID without reconnecting the socket.
  const setConversation = (conversationId: string | null) => {
    sock.current?.send({ type: "conversation.set", conversation_id: conversationId });
  };

  return { state, connected, partial, engine, liveOn, handsFree, handsFreeSupported, toggleHandsFree,
    pressStart, pressStop, interrupt, answer, setConversation };
}

export function VoiceDock({ voice, onInterrupt }: {
  voice: ReturnType<typeof useVoiceSession>;
  onInterrupt?: () => void;
}) {
  const { state, connected, partial, engine, liveOn, handsFree, handsFreeSupported, toggleHandsFree,
    pressStart, pressStop } = voice;
  const label = !connected ? "voce offline"
    : engine === "live" && !liveOn ? "Parla con NODO"
    : engine === "live" && state === "SPEAKING" ? "NODO parla · tocca per interrompere"
    : engine === "live" ? "Ti ascolto · clicca per chiudere"
    : handsFree ? "Ascolto libero attivo · clicca per chiudere"
    : state === "LISTENING" ? "Ti ascolto · clicca per inviare"
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
      {connected && engine === "browser" && handsFreeSupported && (
        <button type="button" className={handsFree ? "handsfree handsfree--active" : "handsfree"}
                aria-pressed={handsFree} onClick={toggleHandsFree}
                title="Una scelta esplicita: chiudo il turno dopo una pausa e riapro il microfono dopo la risposta. Non ascolto mentre NODO parla.">
          {handsFree ? "Chiudi ascolto libero" : "Ascolto libero"}
        </button>
      )}
      {(state === "SPEAKING" || state === "THINKING" || state === "ACTING") && (
        <button type="button" className="ghost" onClick={() => { voice.interrupt(); onInterrupt?.(); }}>Ferma</button>
      )}
      {partial && <span className="partial">{partial}</span>}
    </div>
  );
}
