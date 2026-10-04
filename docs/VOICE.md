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
- Browser STT quality/availability varies (Chrome/Edge good, Firefox has no STT → falls back to audio upload).
- Server-side streaming STT is a seam: only fakes implement `begin/feed/end` today; real audio upload path
  transcribes on `speech.end` (not yet word-by-word).
- `speechSynthesis` has no barge-in midpoint: cancelling restarts at the next sentence.
- No production VAD/turn detector yet; `TurnDetector`/`WakeWordEngine` remain protocols.
- `interrupted` turns still finish DB writes internally (by design — records stay consistent).
