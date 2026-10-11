## Browser hands-free (V06) — conversatione a turni, attivazione esplicita

Quando il motore è browser e STT/TTS del browser sono disponibili, `VoiceDock`
mostra **Ascolto libero**. L'attivazione è un click esplicito, **non è una wake word**.
NODO ricostruisce la trascrizione da ogni snapshot `SpeechRecognition.results`
(Chrome può ripetere risultati finali) e rileva la fine del turno con un intervallo
di silenzio: circa 1,1s per testo finale, 1,7s per testo ancora provvisorio.
Gli intervalli sono euristiche UI, non una VAD acustica professionale.

Il microfono viene sospeso appena un turno è riconosciuto; NODO elabora e
riproduce tutta la coda `speechSynthesis`, **poi** riattiva l'ascolto, purché
l'utente abbia mantenuto il toggle acceso e il WebSocket sia collegato.
`voice.turn.done` da solo NON basta per riaprire il microfono: il browser
potrebbe stare ancora pronunciando l'ultima frase. Se il riconoscimento
termina naturalmente senza parole, si riavvia solo mentre il toggle resta
attivo. Stop volontario, errori di microfono e disconnessioni disattivano
l'ascolto libero. La sessione usa lo stesso `conversation_id` e lo stesso
backend di sicurezza/approvazioni di push-to-talk e testo.

**Limiti dichiarati:** questa è una modalità *half-duplex*, non ascolta
mentre NODO pronuncia la risposta, quindi il barge-in completamente vocale
non è garantito. Il pulsante **Ferma** continua a interrompere NODO.
I browser possono terminare/rifiutare automaticamente la ripartenza del
riconoscimento (limiti gestiti dal browser); perciò questo non equivale a un
servizio locale sempre attivo. Chrome/Edge hanno implementazioni diverse.

**Privacy Web Speech:** NODO salva la trascrizione nel suo database, non
archivia file audio. Il motore Web Speech può però usare un servizio remoto
del browser, a seconda della piattaforma/configurazione: non promettere
«riconoscimento offline» in modalità browser. Per una modalità realmente
locale servono motori STT/TTS on-device configurati esplicitamente.
https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition

**Prove manuali Windows**: parla due volte senza cliccare tra i turni; frase
con risultati final multipli (nessun testo duplicato); risposta lunga con
sintesi a più frasi (il microfono non riapre mentre parla); interruzione
con Ferma; disconnessione WebSocket (ascolto libero non si riattiva senza
consenso); approvazione esplicita; browser senza Web Speech (toggle assente).

## Gemini Live quota: interpretazione e fallback (V04)

Google Gemini API impone limiti **per progetto** (non per singola chiave).
I limiti dipendono anche dal modello e dal tipo di utilizzo; le richieste
giornaliere si azzerano a mezzanotte Pacifico, ma non tutti gli errori di
limite sono necessariamente giornalieri. Consulta i limiti reali su
https://ai.google.dev/gemini-api/docs/rate-limits e in Google AI Studio.

Un WebSocket `1011` da solo **non** dimostra che sia stata esaurita la quota:
serve un messaggio di limite esplicito (`quota`, `RESOURCE_EXHAUSTED`, `429`).
Alla rilevazione NODO interrompe i tentativi Live ripetitivi e passa alla voce
del browser mantenendo il thread. Il cooldown locale di 60 minuti è una scelta
di backoff, **non** il momento di reset della quota; un nuovo tentativo richiede
una nuova sessione (per esempio ricaricare il pannello) dopo il cooldown.
Nessun retry autonomo in background è implementato.

Non proporre chiavi multiple dello stesso progetto come modo per ripristinare
l'uso: condividono la quota del progetto. L'eventuale nuova chiave è soltanto
un'identità di accesso diversa, non una garanzia di quota aggiuntiva.

Il banner di fallback è visibile anche con il diario chiuso. Il client ferma
la riproduzione Live residua, mentre il server scarta i PCM ancora in transito
senza passarli erroneamente come `audio/webm` allo STT browser.

Verifiche manuali Windows: quota esaurita durante una risposta, audio che si
ferma, ritorno a push-to-talk, risposta testuale e voce browser coerenti,
nessuna connessione ripetuta a cinque modelli. Verificare separatamente che
un `1011` privo di messaggio quota abbia comportamento di riconnessione standard.

# Voice

Voice is a first-class interface over the same Core (ADR-005). Phase 2 landed the realtime channel:
**push-to-talk over WebSocket** with incremental transcripts, streamed responses, sentence-level TTS and
real barge-in. Wake word and always-on listening stay architected-only (local Node boundary, ADR-008).

## Realtime flow (implemented)
```
hold mic / Space ─► LISTENING ─► transcript.partial (browser STT or audio.chunk + streaming STT)
        release ─► transcript.final / speech.end ─► UNDERSTANDING ─► THINKING/ACTING (same Core as text)
        nodo.token / nodo.sentence stream ─► SPEAKING (tts.speak browser | tts.chunk server)
        mic press or "fermati" while SPEAKING/THINKING ─► voice.interrupted ─► LISTENING
```

## Live engine — Gemini Live (`nodo/voice/live.py`)
When `NODO_VOICE_ENGINE=auto` (default) and a Gemini key is configured, the voice session upgrades to
**speech-to-speech**: mic PCM (16 kHz) flows straight to Gemini Live, its PCM answer (24 kHz) streams back
as `tts.chunk` events. No STT/LLM/TTS chain — reply latency is ~1 s and **barge-in is native** (you talk
over NODO, Gemini stops itself).

```
click mic ─► session.start ─► LiveBridge.start (persona + Toolbox specs + history) ─► engine: "live"
mic PCM (AudioWorklet, /pcm-worklet.js) ─► audio.chunk ─► send_realtime_input
Gemini ─► audio (pcm) / input+output transcription / tool_call / interrupted / turn_complete
```

- **Tools**: Gemini's `function_calls` run through the *same* `Toolbox` as the text brain — guards,
  approvals and `decide()` are unchanged. Approval answers and acks are injected back into the session
  (`send_client_content`), so NODO *says* "Confermato" instead of the browser reading it.
- **Model ladder**: `LIVE_MODELS` names are tried in order (idea borrowed from JARVIS Mark LV,
  reimplemented); the first the key can open wins. `NODO_GEMINI_LIVE_MODEL` pins one.
- **Fallbacks**: no key / no `google-genai` / connect failure (25 s timeout) / session lost after one
  transparent reconnect → the session answers with `engine: "browser"` and the classic chain works
  untouched. `GET /api/v1/voice/config` reports `engine` and `live_reason`.
- Settings: `NODO_VOICE_ENGINE` (`auto|live|browser`), `NODO_LIVE_VOICE` (Kore, Puck, Charon, Fenrir,
  Aoede…), `NODO_GEMINI_LIVE_MODEL`.
- Typed text (`transcript.final`) works in live mode too — it is injected as a user turn.
- Tests use a fake `container.live_connect` factory (`tests/test_voice_live.py`): the whole bridge,
  tool calls, barge-in, ladder and approval ack are covered without a key.

## V03 — Presence and durable conversation

The command screen is now **voice-first**: a central presence reacts to real WebSocket states
(`LISTENING`, `THINKING`, `ACTING`, `SPEAKING`, `WAITING_FOR_APPROVAL`, etc.).
Tool activity is shown only when a `nodo.step` event indicates actual execution. The
transcription stays available through the **Apri diario** control, but is no longer the main
surface; approvals stay visible even with the diary closed. Animations honor
`prefers-reduced-motion`.

Live input and output transcriptions are persisted as `Message(user)` and `Message(nodo)`
at `turn_complete` (never raw microphone audio). The Live conversation is committed
before its ID is sent to the client. Both speech and text refer to the same
`conversation_id`; `conversation.set` switches the active thread on an existing
WebSocket while no turn or approval is active. When switching away from a Live thread,
the old Live bridge is closed so the next utterance loads the new conversation context.
`voice.conversation` acknowledges the shared ID. A failed DB write emits a visible error.

The browser audio chunk player consumes each queued URL once, revokes URLs after use
or interruption and wakes the pending completion promise on barge-in. The Live
interrupt flag is never cleared merely because the microphone still sends PCM packets.
New speech transcriptions or turn completion restore normal output.

Manual Windows acceptance: verify browser microphone permission, voice-first and
text-first follow-ups, long pauses, `Ferma` during playback, repeated start/stop,
reconnect and approval confirmations. CI cannot validate actual audio device behavior.

## Transport — `WS /api/v1/voice/stream`
Typed JSON frames (`nodo/voice/protocol.py`, pydantic-validated); binary frames count as `audio.chunk`.
Auth mirrors REST: `NODO_API_TOKEN` via `?token=` or `Authorization` header.

**Client → server:** `session.start`, `audio.chunk`, `speech.end`, `transcript.partial`, `transcript.final`,
`interrupt`, `approval.answer`, `session.end`.

**Server → client:** `voice.session.started|state|closed`, `transcript.partial|final` (echo or server STT),
`nodo.thinking|intent|plan|context|step`, `agent.*`, `tool.*`, `nodo.token|sentence|response`,
`tts.started|speak|chunk|finished`, `voice.interrupted`, `voice.turn.done` (with `latency_ms`),
`approval.required|resolved|ignored`, `error`.

Every frame carries `session_id`, `turn`, `seq`. `VoiceRuntime` (`nodo/voice/runtime.py`) is transport-
agnostic: it takes an `emit` callable and parsed inbound messages.

## Session lifecycle
`CONNECTING → IDLE → LISTENING → TRANSCRIBING → UNDERSTANDING → THINKING/ACTING → SPEAKING → IDLE`,
plus `INTERRUPTED`, `WAITING_FOR_APPROVAL`, `ERROR`, `CLOSED`. Legal transitions are enforced by
`VoiceSession.transition()` (`nodo/voice/base.py`); every transition is observable as `voice.session.state`.

## Turn semantics
- Turns execute **only on final transcripts** (or `speech.end` with server-side STT). Partials are echoed
  for UI feedback, never sent to the Core.
- **Interrupt** is a real event: bumps the turn generation so in-flight pipeline events are suppressed,
  drains the speech queue, cancels playback client-side, and lands in LISTENING. Backend records finish
  consistently; nothing else reaches the wire.
- **Fast stop path**: "fermati / basta / stop / annulla / silenzio …" matches a dedicated regex before the
  Core pipeline — no inference, near-zero latency. Also covers interruption *during* generation.
- **Approvals are bound**: a spoken "sì"/"no" resolves only `session.pending_approval_id`. A bare yes/no
  with nothing pending emits `approval.ignored` and is never an authorization.
- **Context continuity**: `conversation_id` flows through `Core.handle(..., channel="voice")`; follow-ups
  ("E PB CARe?", "quale dei due…") reuse `active_context` exactly like text.

## Where speech is processed
`GET /api/v1/voice/config` reports `stt.location` / `tts.location`:
- `browser` (default, FREE): Web Speech API STT + `speechSynthesis`. Audio never leaves the device; the
  client sends `transcript.partial/final` over the socket. Browser TTS receives `tts.speak` text events.
- `cloud` (`openai`): audio chunks buffered → `/voice/transcribe` contract inside the runtime; `tts.chunk`
  streams base64 audio back. Refused in `NODO_MODE=FREE` unless `NODO_ALLOW_PAID_IN_FREE_MODE=true`.
- `local` (planned): Whisper/Piper or any `StreamingSTT/TTS` implementation on a NODO Node.

## Providers (`nodo/voice/providers.py`)
`SpeechToTextProvider` / `TextToSpeechProvider` (batch) + `StreamingSpeechToTextProvider` /
`StreamingTextToSpeechProvider` protocols (`begin(language)` → `feed(chunk)` → `end()` → `SpeechEvent`s;
`synthesize_stream` → `AudioChunk`s). Deterministic `FakeStreamingSTT`/`FakeStreamingTTS` cover tests with
no credentials.

## Latency telemetry
`VoiceSession.mark()` stamps: `speech_started`, `speech_end`, `transcript_partial_first`, `transcript_final`,
`intent_resolved`, `context_built`, `model_first_token`, `model_done`, `tts_started`, `tts_first_audio`,
`tts_done`, `turn_done`. Persisted to `voice_turn_metrics` per turn; `voice.turn.done` echoes `latency_ms`.

## Privacy
- Mic state is explicit: the dock shows the real session state; audio is captured only while held
  (push-to-talk) or between `speech` boundaries.
- Raw audio is never persisted; transcripts become `Message` rows like typed text.
- No always-on listening. Wake word ("Node.") is reserved for a local Node — never continuous cloud upload.

## Push-to-talk UX (`apps/web/src/components/Voice.tsx`)
Hold the mic button or Space. Web Speech API produces partials live; without it, `MediaRecorder` streams
`audio.chunk` (250 ms slices). During SPEAKING/THINKING a press sends `interrupt` (barge-in) and the
`Ferma` button does the same. Approvals render as a banner bound to `approval_id`.

## Known limitations
- Live mode needs a Gemini key and keeps the mic stream open while the conversation is on (explicit click,
  visible state); Live sessions expire (~10 min) and reconnect once transparently.
- Browser STT quality/availability varies (Chrome/Edge good, Firefox has no STT → falls back to audio upload).
- Server-side streaming STT is a seam: only fakes implement `begin/feed/end` today; real audio upload path
  transcribes on `speech.end` (not yet word-by-word).
- `speechSynthesis` has no barge-in midpoint: cancelling restarts at the next sentence.
- No production VAD/turn detector yet; `TurnDetector`/`WakeWordEngine` remain protocols.
- `interrupted` turns still finish DB writes internally (by design — records stay consistent).
