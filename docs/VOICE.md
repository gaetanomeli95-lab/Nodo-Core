# Voice

Voice is a first-class interface over the same Core. V0.1 ships **push-to-talk**; the abstractions already
cover streaming, wake word and barge-in (ADR-005).

## V0.1 flow
```
hold mic / Space ─► LISTENING ─► release ─► UNDERSTANDING (STT) ─► POST /command (SSE) ─► THINKING/ACTING
                                                                       └─► done ─► SPEAKING (TTS) ─► IDLE
pressing mic while SPEAKING = barge-in: playback cancelled, POST /voice/sessions/{id}/interrupt, LISTENING
```

## Where speech is processed
`GET /api/v1/voice/config` tells the client the `location` of STT/TTS:
- `browser` (default, FREE): Web Speech API on-device (Chrome/Edge; Safari partial; Firefox no STT). Audio never
  leaves the device. Client sends the transcript as `text` to `/voice/transcribe` so the server still records the
  turn through the same contract.
- `cloud` (`openai`): MediaRecorder audio uploaded to `/voice/transcribe`; `/voice/speak` returns MP3. Refused in
  FREE mode unless `NODO_ALLOW_PAID_IN_FREE_MODE=true`.
- `local` (future): Whisper/Piper on a NODO Node, same interfaces.

## Interfaces (`nodo/voice/base.py`)
- `SpeechToTextProvider.transcribe(audio, mime, language) -> Transcript`
- `TextToSpeechProvider.synthesize(text, voice, language) -> AudioResult`
- `VoiceSession` with states `IDLE, LISTENING, UNDERSTANDING, THINKING, ACTING, SPEAKING, WAITING_FOR_APPROVAL, ERROR`,
  legal transitions enforced, `interrupt()` always legal.
- `WakeWordEngine`, `TurnDetector`: protocols only (Phase 2/5).

## Next
WebSocket `/voice/stream` (incremental transcript + chunked audio), sentence-level TTS while tokens stream,
voice selection, Italian-first voices, VAD/turn detection, wake word on a local Node (never always-on cloud).
