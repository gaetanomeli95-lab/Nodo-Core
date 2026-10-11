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
  private generation = 0;
  private finish: (() => void) | null = null;
  constructor(private onPlaying: (playing: boolean) => void) {}

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
    this.onPlaying(true);
    const gen = this.generation;
    while (this.queue.length && gen === this.generation) {
      const url = this.queue.shift()!;
      const audio = new Audio(url);
      this.current = audio;
      await new Promise<void>((resolve) => {
        this.finish = resolve;
        audio.onended = () => resolve(); audio.onerror = () => resolve();
        void audio.play().catch(() => resolve());
      });
      URL.revokeObjectURL(url);
    }
    if (gen !== this.generation) return;
    this.playing = false;
    this.onPlaying(false);
    this.current = null;
  }
  stop() {
    this.generation += 1;
    this.queue.forEach((url) => URL.revokeObjectURL(url));
    this.queue = [];
    this.current?.pause();
    this.finish?.();
    this.finish = null;
    this.current = null;
    this.playing = false;
    this.onPlaying(false);
  }
}

/** Streaming PCM playback for Gemini Live audio (`audio/pcm;rate=24000`): chunks are scheduled
 *  back-to-back on an AudioContext so speech starts with the first chunk and has no gaps. */
class PcmPlayer {
  private ctx: AudioContext | null = null;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private analyser: AnalyserNode | null = null;
  private samples = new Float32Array(256);
  constructor(private onPlaying: (playing: boolean) => void) {}

  async unlock() {
    if (!this.ctx || this.ctx.state === "closed") {
      this.ctx = new AudioContext();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.connect(this.ctx.destination);
    }
    await this.ctx.resume();
  }

  level() {
    if (!this.analyser || !this.sources.size) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    return Math.sqrt(this.samples.reduce((sum, n) => sum + n * n, 0) / this.samples.length);
  }

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
    if (!this.ctx || !this.analyser) return;
    if (this.ctx.state === "suspended") void this.ctx.resume();
    const buf = this.ctx.createBuffer(1, f32.length, rate);
    buf.copyToChannel(f32, 0);
    const s = this.ctx.createBufferSource();
    s.buffer = buf;
    s.connect(this.analyser);
    const t = Math.max(this.ctx.currentTime + 0.02, this.next);
    s.start(t);
    this.next = t + buf.duration;
    this.sources.add(s);
    this.onPlaying(true);
    s.onended = () => { this.sources.delete(s); if (!this.sources.size) this.onPlaying(false); };
  }

  stop() {
    for (const s of this.sources) { try { s.stop(); } catch { /* already ended */ } }
    this.sources.clear();
    this.next = this.ctx ? this.ctx.currentTime : 0;
    this.onPlaying(false);
  }
  dispose() {
    this.stop();
    if (this.ctx) void this.ctx.close();
    this.ctx = null; this.analyser = null;
  }
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
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const inputLevel = useRef(0);
  const micStarting = useRef(false);
  const micEpoch = useRef(0);
  const sock = useRef<VoiceSocket | null>(null);
  const rec = useRef<Recognition | null>(null);
  const player = useRef(new ChunkPlayer(setAudioPlaying));
  const held = useRef(false);
  const toggled = useRef(false);
  const listening = useRef(false);   // browser speech recognition running for this press     // started with a click: listening until the next click
  const pressedAt = useRef(0);
  const speechQueue = useRef<string[]>([]);
  const speaking = useRef(false);
  const finalBuf = useRef("");
  const streamingStarted = useRef(false);
  const media = useRef<{ rec: MediaRecorder; stream: MediaStream } | null>(null);
  const pcm = useRef(new PcmPlayer(setAudioPlaying));
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
    if (!text) { speaking.current = false; setAudioPlaying(false); return; }
    if (!("speechSynthesis" in window)) {
      speaking.current = false;
      ttsFail("questo browser non può leggere le risposte ad alta voce: usa Chrome o Edge");
      return;
    }
    speaking.current = true;
    setAudioPlaying(true);
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
    setConnected(false); setLiveOn(false); setRecovering(false);
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
              setEngine(e.engine === "live" ? "live" : "browser");
              setConnected(true);
              setRecovering(false);
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
            case "voice.engine":       // live session died mid-conversation: back to push-to-talk
              setRecovering(false);
              setEngine(e.engine === "live" ? "live" : "browser");
              if (e.engine !== "live") { stopLiveMic(); stopLocalAudio(); setLiveOn(false); setState("IDLE"); }
              break;
            case "voice.connection":
              setRecovering(e.status === "reconnecting");
              break;
            case "tts.chunk":
              if ((e.mime as string || "").startsWith("audio/pcm")) pcm.current.push(e.audio as string, e.mime as string);
              else player.current.push(e.audio as string, e.mime as string);
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
        () => {
          // NODO restarted or the network dropped: try again (2s, 4s, ... up to 15s)
          setConnected(false);
          setState("CONNECTING");
          stopLocalAudio(); stopLiveMic(); setLiveOn(false);
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
    return () => {
      closed = true; if (retry) clearTimeout(retry); sock.current?.close();
      stopLocalAudio(); stopLiveMic(); pcm.current.dispose();
      if (rec.current) { rec.current.onend = null; rec.current.onresult = null; rec.current.onerror = null; rec.current.stop(); rec.current = null; }
      listening.current = false; held.current = false; toggled.current = false; setCapturing(false);
      media.current?.stream.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  const stopLocalAudio = () => {
    speechQueue.current = [];
    speaking.current = false;
    setAudioPlaying(false);
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    player.current.stop();
    pcm.current.stop();
  };

  // ---------------------------------------------------------- Gemini Live: mic streaming
  const startLiveMic = async (): Promise<boolean> => {
    if (micStarting.current || liveMic.current) return false;
    micStarting.current = true;
    const epoch = ++micEpoch.current;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    try {
      await pcm.current.unlock(); // create/resume playback from the user's gesture
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      ctx = new AudioContext();
      await ctx.resume();
      await ctx.audioWorklet.addModule("/pcm-worklet.js");
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "pcm-worklet");
      node.port.onmessage = (ev: MessageEvent) => {
        if (epoch !== micEpoch.current) return;
        const pcm16 = new Uint8Array(ev.data as ArrayBuffer);
        const samples = new Int16Array(ev.data as ArrayBuffer);
        inputLevel.current = Math.sqrt(samples.reduce((sum, n) => sum + (n / 32768) ** 2, 0) / samples.length);
        if (pcm16.length) sock.current?.send({ type: "audio.chunk", data: toB64(pcm16), mime: "audio/pcm;rate=16000" });
      };
      src.connect(node);
      const silent = ctx.createGain();
      silent.gain.value = 0;
      node.connect(silent);
      silent.connect(ctx.destination);
      if (epoch !== micEpoch.current) {
        stream.getTracks().forEach((t) => t.stop()); void ctx.close(); return false;
      }
      liveMic.current = { ctx, stream };
      return true;
    } catch {
      stream?.getTracks().forEach((t) => t.stop());
      if (ctx) void ctx.close();
      cbRef.current.onError("microfono non disponibile: consenti l'accesso dal lucchetto vicino all'indirizzo e riprova");
      return false;
    } finally { micStarting.current = false; }
  };

  const stopLiveMic = () => {
    micEpoch.current += 1;
    inputLevel.current = 0;
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

  const pressStart = () => {
    if (!connected) return;
    if (engine === "live") { void liveButton(); return; }
    if (toggled.current) { toggled.current = false; finishListening(); return; }  // second click: send
    if (state === "SPEAKING" || state === "THINKING" || state === "ACTING") interrupt(); // barge-in
    if (state !== "IDLE" && state !== "LISTENING" && state !== "INTERRUPTED" && state !== "CONNECTING"
        && state !== "ERROR") return;
    held.current = true;
    setCapturing(true);
    pressedAt.current = Date.now();
    setState("LISTENING");
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
        finalBuf.current = fin; // SpeechRecognition results are cumulative for this session
        const live = (finalBuf.current + " " + interim).trim();
        if (live) sock.current?.send({ type: "transcript.partial", text: live });
      };
      r.onerror = (ev: unknown) => {
        const why = (ev as { error?: string } | null)?.error;
        if (why === "no-speech" || why === "aborted") return;
        held.current = false; toggled.current = false; listening.current = false;
        setCapturing(false);
        setState("IDLE");
        cbRef.current.onError(why === "not-allowed" || why === "service-not-allowed"
          ? "il browser non mi lascia usare il microfono: clicca il lucchetto vicino all'indirizzo e consenti il microfono, poi ricarica la pagina"
          : why === "network" ? "il riconoscimento vocale del browser non è raggiungibile (serve internet): prova con Chrome o Edge aggiornati"
          : why === "audio-capture" ? "nessun microfono trovato: controlla che sia collegato e attivo in Windows"
          : `riconoscimento vocale non riuscito (${why ?? "errore sconosciuto"})`);
      };
      // ends when you release/click again, or by itself after a long silence: either way send what was heard
      r.onend = () => { if (listening.current) { listening.current = false; commit(); } };
      rec.current = r;
      listening.current = true;
      try { r.start(); } catch {
        listening.current = false; held.current = false; toggled.current = false; setState("IDLE");
        setCapturing(false);
        cbRef.current.onError("il riconoscimento vocale non è partito: riprova tra un attimo");
      }
    } else {
      // no browser STT: capture audio and let the server transcribe on release
      void startAudioCapture();
    }
  };

  const startAudioCapture = async () => {
    const epoch = ++micEpoch.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (epoch !== micEpoch.current || !held.current && !toggled.current) {
        stream.getTracks().forEach((t) => t.stop()); return;
      }
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      let chunks = Promise.resolve();
      mr.ondataavailable = (e) => {
        chunks = chunks.then(async () => {
          const buf = await e.data.arrayBuffer();
          const bytes = new Uint8Array(buf);
          let bin = "";
          for (const b of bytes) bin += String.fromCharCode(b);
          sock.current?.send({ type: "audio.chunk", data: btoa(bin), mime: "audio/webm" });
        });
      };
      mr.onstop = () => { void chunks.then(() => sock.current?.send({ type: "speech.end" })); };
      mr.start(250); // chunk every 250ms
      media.current = { rec: mr, stream };
    } catch { setCapturing(false); setState("IDLE"); cbRef.current.onError("microfono non disponibile"); }
  };

  const stopAudioCapture = () => {
    micEpoch.current += 1;
    setCapturing(false);
    if (!media.current) return;
    media.current.rec.stop();
    media.current.stream.getTracks().forEach((t) => t.stop());
    media.current = null;
  };

  const commit = () => {
    setCapturing(false);
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
  };

  const answer = (approvalId: string, decision: "approve" | "reject") =>
    sock.current?.send({ type: "approval.answer", approval_id: approvalId, decision });

  const sendText = async (text: string) => {
    await pcm.current.unlock();
    sock.current?.send({ type: "transcript.final", text });
  };

  const toggleMic = () => { pressStart(); if (engine !== "live") pressStop(); };
  const endConversation = () => {
    sock.current?.send({ type: "interrupt", reason: "microphone_off" });
    stopLiveMic(); stopLocalAudio(); setLiveOn(false); setState("IDLE");
    sock.current?.send({ type: "speech.end" });
  };

  const setConversation = (conversationId: string | null) => {
    sock.current?.send({ type: "conversation.set", conversation_id: conversationId });
  };

  // push-to-talk on Space
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !(e.target as HTMLElement).closest("input, textarea, button, select, a, [contenteditable]") && !e.repeat) {
        e.preventDefault(); pressStart();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && !(e.target as HTMLElement).closest("input, textarea, button, select, a, [contenteditable]")) pressStop();
    };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  });

  return { state, connected, partial, engine, liveOn, audioPlaying, recovering, pressStart, pressStop,
    interrupt, answer, sendText, toggleMic, endConversation, setConversation, microphoneOn: liveOn || capturing,
    sampleLevel: () => ({ input: inputLevel.current, output: pcm.current.level() }) };
}

export function VoiceDock({ voice, onInterrupt }: {
  voice: ReturnType<typeof useVoiceSession>;
  onInterrupt?: () => void;
}) {
  const { state, connected, partial, engine, liveOn, pressStart, pressStop } = voice;
  const label = !connected ? "voce offline"
    : engine === "live" && !liveOn ? "Parla con NODO"
    : engine === "live" && state === "SPEAKING" ? "NODO parla · tocca per interrompere"
    : engine === "live" ? "Ti ascolto · clicca per chiudere"
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
      {(state === "SPEAKING" || state === "THINKING" || state === "ACTING") && (
        <button type="button" className="ghost" onClick={() => { voice.interrupt(); onInterrupt?.(); }}>Ferma</button>
      )}
      {partial && <span className="partial">{partial}</span>}
    </div>
  );
}
