# Roadmap

Each phase lists what exists in the code *now* as a seam, so later work extends rather than rewrites.

## Phase 0 — Foundation ✅ (this branch)
Architecture, repo structure, migrations, CI, docs, ADRs, Core interfaces, deterministic test adapters.

## Phase 1 — Memory + Command ✅ (V0.1 slice)
Entities, projects, tasks, decisions, facts with provenance, relationships, text command with SSE,
entity resolution, conversational references, Context Engine, telemetry.
*Next within phase:* model-backed intent classifier behind `IntentEngine.classify`; fact extraction from
conversations (NODO MEMORY agent); staleness job.

## Phase 2 — Voice (push-to-talk ✅ → realtime transport ✅ → streaming providers)
Done on this branch: typed WS protocol (`/voice/stream`), `VoiceRuntime` over the same Core, partial/final
transcripts, token+sentence streaming, sentence-level TTS (browser `tts.speak` + `tts.chunk` seam),
barge-in with generation suppression, fast stop-command path, bound voice approvals, `voice_turn_metrics`
latency telemetry, conversational continuity (incl. `compare` intent).
Seams: `StreamingSTT/TTS` protocols + fakes, `TurnDetector`, `WakeWordEngine`, provider `location`.
Next: real streaming STT (faster-whisper/local), silence-based end-of-turn, selectable voices,
wake word on a local Node.

## Phase 3 — Agents (PM/DEV/CONTENT ✅ → richer)
Seams: `Agent`, `AgentContext`, `Budget`, `AgentRunner`, `AgentReport`.
Next: RESEARCH, MEMORY, OPS, GUARD agents; dynamic sub-agents (child budget already modelled);
agent memory (`MemoryFact.layer = "agent"`).

## Phase 4 — Connectors (GitHub read ✅ → writes + more)
Seams: `ToolSpec`/`ToolAction` with levels, `ToolExecutor` with approvals, `Approval` records + API.
Next: execute approved actions (resume `ToolExecution` on `approval.approved`); GitHub issues/PR comments;
Google Drive/Calendar/Gmail; documents ingestion → `Document` + facts; coding-agent execution provider.

## Phase 5 — Local Node
Seams: provider `is_local`, `max_sensitivity`, `PRIVATE` mode, `WakeWordEngine` protocol, voice provider
`location`. See `LOCAL_NODE.md` for the Node protocol boundary.

## Phase 6 — Proactive NODO
Seams: append-only `event_log`, `EventBus.subscribe`, Pulse panel computed from real tasks.
Next: job runner (in-process first), Pulse Engine scoring (importance/urgency/relevance/confidence),
automations (trigger → conditions → actions), daily briefing.

## Phase 7 — Distributed NODO
Node registry, capability advertisement, workload placement (privacy/latency/cost), mobile client over the
same API (already API-first; `/command/sync` exists for constrained clients).
