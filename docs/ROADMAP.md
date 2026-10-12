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

## Phase 4 — Connectors (GitHub read ✅ → GitHub/Vercel writes ✅ → more)
Done: approved actions are executed (REST + voice), GitHub branch/commit/PR/merge/close/issues, Vercel
deploys/logs/rollback/promote/redeploy (night shift, ADR-009).
Seams: `ToolSpec`/`ToolAction` with levels, `ToolExecutor` with approvals, `Approval` records + API.
Done: personal knowledge import (ChatGPT/Claude exports → `Document` + FTS chunks → distilled facts with
provenance, `docs/CONOSCENZA.md`).
Next: GitHub issues/PR comments; Gmail read-only → same ingestion path; Google Drive/Calendar; fact
confirmation UI; coding-agent execution provider.

## Phase 4b — Brain ✅
- Tool-calling across providers (Anthropic, OpenAI-compatible incl. Gemini/Groq, Claude subscription via Claude Code)
- Conversation loop with memory, state snapshot, guarded decisions, background site changes (docs/CERVELLO.md)
- Next: free voice stack (local Whisper STT, natural TTS, wake word), streaming answers for voice, desktop app

## Phase 5 — Local Node
Seams: provider `is_local`, `max_sensitivity`, `PRIVATE` mode, `WakeWordEngine` protocol, voice provider
`location`. See `LOCAL_NODE.md` for the Node protocol boundary.

## Phase 6 — Proactive NODO
Seams: append-only `event_log`, `EventBus.subscribe`, Pulse panel computed from real tasks.
Next: job runner (in-process first), Pulse Engine scoring (importance/urgency/relevance/confidence),
automations (trigger → conditions → actions). Done: in-process job runner + morning briefing (night shift).
V05 (PR): **Pulse radar** deterministico da task, incidenti, approvazioni e notte, con fonti per ogni segnale,
API read-only e handoff conversazionale da Presence. Nessuna verifica esterna simulata.
Next: Log Drains per telemetria completa, sincronizzazione eventi, priorità configurabili,
notifiche non invasive e playbook di rimedio supervisionati.

## Phase 7 — Distributed NODO
Node registry, capability advertisement, workload placement (privacy/latency/cost), mobile client over the
same API (already API-first; `/command/sync` exists for constrained clients).
