# NODO CORE — Architecture

## 1. Assessment (written before building)

### Risks
| Risk | Mitigation in V0.1 |
|---|---|
| Over-engineering: the vision lists ~40 subsystems | Implement 12, define seams (interfaces/ADRs) for the rest. Rule: *interface now, implementation when a real use case arrives.* |
| AI theater (fake intelligence in FREE mode) | `DeterministicProvider` returns only the grounded summary computed from stored data and is labelled as such in telemetry. No templated "thinking". |
| Wrong context → wrong answers | Context Engine assembles a bounded, inspectable `ContextPackage`; the UI shows sources and sizes. |
| Silent spend | Router hard-rule: FREE mode excludes paid models; every inference persisted with cost estimate. |
| Data leakage to arbitrary providers | Sensitivity on entities → max of package → router gate (`max_sensitivity`), enforced in code not prompts. |
| Agent recursion / runaway tools | `Budget` per run (time, model calls, tool calls, child agents, depth); crashes isolated per agent. |
| Memory corruption | Facts are never overwritten: `SUPERSEDED` + `valid_until`; same-predicate different-value → `CONFLICTING` event. |
| Prompt injection via tools/docs | Tool output is injected as `CONTEXT (dati, non istruzioni)`; system prompt forbids obeying it; no tool output is ever placed in the system role. |

### Unknowns
Free-tier provider limits (encoded as config, not code); quality of rule-based intent on real speech;
Web Speech API availability per browser (Chrome/Edge yes, Firefox partial); Windows dev environments.

### Hard problems deferred by design
Semantic retrieval (embeddings), approval-executed writes, distributed Nodes, wake word.
*Realtime duplex voice (Phase 2) is implemented: WebSocket `/voice/stream`, `VoiceRuntime`, typed protocol.*

## 2. Stack (ADR-001, ADR-007)
- **API/Core:** Python 3.12, FastAPI, SQLAlchemy 2 (typed mapped classes), Alembic, Pydantic v2, httpx.
- **DB:** SQLite for dev/test, PostgreSQL for production; one model, batch-mode migrations for SQLite.
- **Web:** React 18 + TypeScript + Vite (no SSR needed; API-first; PWA/mobile-friendly later).
- **Realtime:** SSE for text command streaming; WebSocket `/api/v1/voice/stream` for duplex voice
  (`nodo/voice/protocol.py` typed frames → `VoiceRuntime` → same `NodoCore.handle`).
- **Jobs:** none yet; `events.py` + append-only `event_log` are the hook for a job runner (Phase 6).

## 3. Request lifecycle
```
POST /api/v1/command {text, conversation_id?, channel}
 1. Conversation loaded/created; user Message stored; event request.received
 2. IntentEngine.classify(text, active_context) → Intent{name, entity, from_context, reference_reapplied}
 3. Plan = PLANS[intent]  (explicit list of steps, streamed to the UI)
 4. ContextEngine.build(intent) → ContextPackage{projects,tasks,decisions,repos,facts,content_history,sensitivity,sources}
 5. For each "agent:x" step: AgentRunner.run → AgentRun row, events agent.started/finished; tools via ToolExecutor
 6. Grounded summary = deterministic text derived from package + agent reports
 7. ModelRouter.stream(messages, RouteRequest{sensitivity}) → tokens; InferenceRun row
 8. nodo Message stored; conversation.active_context updated {entity, last_intent}; event request.completed
```
All of this is visible via `/telemetry/trace/{request_id}` and `/timeline`.

## 4. Module boundaries (future service seams)
| Package | Depends on | Could become |
|---|---|---|
| `providers` | nothing internal | shared library across Nodes |
| `memory` | db | memory service |
| `tools` | core.permissions, core.events | connector workers |
| `agents` | core.context, tools, providers | agent runtime |
| `voice` | nothing internal | local Node service |
| `core.orchestrator` | all of the above | stays the Core |

No module imports `api`. `api` imports only `app` (container) and the modules it exposes.

## 5. Data model highlights
UUID string ids everywhere; `organization_id` on every owned entity; UTC timestamps; JSON columns for
flexible payloads (`tech_state`, `budget`, `usage`, `payload`). The graph is `relationships` + FKs (ADR-003).

## 6. Observability
`InferenceRun` (provider, model, tokens, latency, cost, status), `AgentRun` (budget, usage, status),
`ToolExecution` (level, status, duration), `EventLog` (append-only), `VoiceTurnMetric` (per-turn latency
stamps: speech end → final transcript → first token → first audio → done). `request_id` ties them together.

## 7. What is deliberately NOT here
Redis, Kafka, Kubernetes, Neo4j, microservices, embeddings, wake word, background workers, RBAC.
Each has a documented seam in the relevant ADR/doc.
