# ADR-005 — Voice architecture: a transport into the Core, not a separate assistant

**Status:** accepted · **Date:** 2026-10-04 · **Amended:** Phase 2 realtime channel implemented on
`integration/nodo-core-v01` (WebSocket `/voice/stream`, typed protocol, VoiceRuntime, barge-in, bound
approvals, per-turn latency metrics)

## Context
Voice is a primary interface of NODO, not a feature bolted onto a chat box. The failure mode to avoid is a
"voice chatbot" with its own prompt, its own memory and its own permission logic. The other failure mode is the
batch pipeline (record → upload → wait → text → wait → audio file) that makes conversation impossible. Speech
providers (browser, local Whisper/Piper, cloud APIs, realtime multimodal models) differ enormously in cost,
privacy and latency, and FREE mode must remain valid.

## Decision
1. **Voice is a transport.** Spoken input becomes text and enters the same `NodoCore.handle()` as typed input:
   same Intent Engine, Context Engine, Orchestrator, permissions, telemetry. There is no voice-specific
   reasoning path. `Conversation.channel="voice"` is the only difference recorded.
2. **Speech providers are replaceable.** `SpeechToTextProvider`, `TextToSpeechProvider` protocols with
   `name` and `location ∈ {browser, local, cloud}`. The server tells the client where speech is processed
   (`GET /voice/config`) so audio never travels unnecessarily.
3. **Explicit `VoiceSession` state machine** with legal transitions enforced in code and a traceable history.
   The UI reflects the real state; it does not animate.
4. **Interruption is a first-class event**, always legal from any state, flagged on the session so in-flight
   speech/generation can be cancelled rather than merely muted.
5. **FREE-first.** Default providers are browser-side (Web Speech API, `speechSynthesis`): free, on-device.
   Paid speech providers are refused in `FREE` mode unless `NODO_ALLOW_PAID_IN_FREE_MODE=true`.
6. **Privacy by default.** Raw microphone audio is never persisted by the server; transcripts are stored as
   `Message` rows like typed text. Always-on listening and wake word are explicitly out of scope until they can
   run locally (ADR-008).
7. **Realtime duplex transport is WebSocket** (`/voice/stream`), reserved in ADR-001; SSE remains for text
   commands. Events on that channel follow a typed schema shared with the text channel where meaningful.

## Alternatives considered
- **Vendor realtime API (e.g. OpenAI Realtime) as the voice core.** Rejected as the core: ties identity,
  memory and permissions to one paid vendor and makes FREE mode impossible. Acceptable later as *one*
  provider behind the streaming protocols.
- **Separate voice service with its own model and memory.** Rejected: duplicates the Core, splits context,
  bypasses permissions.
- **WebRTC.** Deferred: superior for media transport but heavier to operate; WebSocket carries PCM/opus
  chunks and typed events adequately for a single user on LAN/localhost. Revisit for mobile/remote Nodes.
- **Server-side only STT (upload WAV).** Kept as an option (`location=cloud|local`), not the default.

## Consequences
+ One intelligence for text and voice; voice gets every Core improvement for free.
+ Zero-cost default path; providers are configuration.
+ State is inspectable (`history`, events), enabling honest UI and latency telemetry.
− Browser speech APIs vary (Chrome/Edge good, Firefox lacks STT); quality depends on the device.
− Push-to-talk is less fluid than hands-free; hands-free requires local VAD/wake word to be acceptable.
− Barge-in across a network requires the backend to really cancel generation, not just the client to mute.

## Current implementation (implemented)

### V0.1 (batch push-to-talk, still present)
- `nodo/api/voice.py`: `/voice/config`, `/voice/sessions`, `/voice/transcribe`, `/voice/speak`.

### Phase 2 realtime (this branch)
- `nodo/voice/protocol.py`: typed inbound frames (`session.start`, `audio.chunk`, `speech.end`,
  `transcript.partial|final`, `interrupt`, `approval.answer`, `session.end`) and the canonical outbound
  envelope (`session_id`, `turn`, `seq`) — see `docs/VOICE.md` for the full event list.
- `nodo/voice/base.py`: extended `VoiceState` (adds CONNECTING, TRANSCRIBING, INTERRUPTED, CLOSED),
  `VoiceSession` with turn counters, latency `mark()`/`stamps`, `pending_approval_id`, `turn_active`.
- `nodo/voice/runtime.py`: `VoiceRuntime` — transport-agnostic orchestration: partial echo, final-only
  turn execution through `NodoCore.handle(channel="voice")`, sentence queue → `tts.speak`/`tts.chunk`,
  generation-scoped suppression on interrupt, fast stop-command path (regex, no inference), bound
  approval resolution, `voice_turn_metrics` persistence per turn.
- `nodo/api/voice_ws.py`: `WS /api/v1/voice/stream` with token auth (`?token=` for browsers).
- `nodo/voice/providers.py`: `StreamingSpeechToTextProvider`/`StreamingTextToSpeechProvider` protocols +
  deterministic `FakeStreamingSTT`/`FakeStreamingTTS`; batch providers unchanged.
- `apps/web/src/components/Voice.tsx`: `useVoiceSession` hook + `VoiceDock` (push-to-talk, Web Speech
  partials, MediaRecorder fallback, `speechSynthesis`/`tts.chunk` playback, barge-in, approval banner).
- Frontend `vite.config.ts` proxies `/api` with `ws: true`.

## Architecturally prepared (seams exist, partial or no implementation)
- `TurnDetector` and `WakeWordEngine` protocols; `speech.end` is the client-driven end-of-turn signal.
- `StreamingSTT` is exercised via fakes; real audio today buffers and transcribes on `speech.end`.
- Provider `location` routing between browser/local/cloud.

## Future evolution (planned)
- Real streaming STT (faster-whisper / cloud realtime) behind `StreamingSpeechToTextProvider`.
- Silence-based `TurnDetector`; hands-free mode only when a local VAD is reliable.
- Wake word ("Node.") only on a local Node (ADR-008), never as continuous cloud upload.
- WebRTC reconsidered for remote/mobile Nodes if WebSocket audio proves limiting.
