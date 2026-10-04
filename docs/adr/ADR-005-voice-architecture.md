# ADR-005 — Voice architecture: a transport into the Core, not a separate assistant

**Status:** accepted · **Date:** 2026-10-04 (V0.1 push-to-talk; Phase 2 realtime in progress on this branch)

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

## Current V0.1 implementation (implemented)
- `nodo/voice/base.py`: `Transcript`, `AudioResult`, `SpeechToTextProvider`, `TextToSpeechProvider`,
  `VoiceState` (IDLE, LISTENING, UNDERSTANDING, THINKING, ACTING, SPEAKING, WAITING_FOR_APPROVAL, ERROR),
  `TRANSITIONS`, `VoiceSession.transition()/interrupt()`, `VoiceSessionStore` (in-memory).
- `nodo/voice/providers.py`: `FakeSTT/FakeTTS` (tests), `BrowserSTT/BrowserTTS`, `OpenAISpeech` (paid,
  gated), `build_voice()` enforcing FREE mode.
- `nodo/api/voice.py`: `/voice/config`, `/voice/sessions` (create/get/state/interrupt), `/voice/transcribe`,
  `/voice/speak`. Push-to-talk UI in `apps/web/src/components/VoiceButton.tsx`.
- Flow: hold → LISTENING → release → UNDERSTANDING → `POST /command` (SSE) → SPEAKING → IDLE; mic during
  SPEAKING cancels playback and posts `/interrupt`.

## Architecturally prepared (seams exist, no implementation)
- `TurnDetector` and `WakeWordEngine` protocols.
- Provider `location` and `VoiceSession.conversation_id` linking to text context.

## Future evolution (planned — Phase 2 "Realtime Voice", starting on this branch)
- Extended session lifecycle (CONNECTING, TRANSCRIBING, INTERRUPTED, CLOSED) with traceable transitions.
- Typed event protocol over WebSocket `/voice/stream` (session, audio chunks, partial/final transcripts, tokens,
  TTS chunks, interrupt, approval, error).
- `StreamingSpeechToTextProvider` (partial transcripts) and `StreamingTextToSpeechProvider` (sentence-level
  audio while tokens stream), each with deterministic fakes; browser and local implementations first.
- `TurnDetector` implementations: push-to-talk (reliable default), silence-based end-of-turn.
- Barge-in that cancels backend generation/TTS and persists the partial assistant turn.
- Fast interrupt command path ("fermati", "stop", "basta") that bypasses the full reasoning pipeline.
- Typed voice approvals bound to a pending `Approval` id; arbitrary "sì" is never authorisation.
- Latency telemetry per turn (speech end → final transcript → first token → first audio → done).
- Wake word ("Node.") only on a local Node (ADR-008), never as continuous cloud upload.

This ADR will be amended when Phase 2 lands with the implemented protocol and the resulting state machine.
