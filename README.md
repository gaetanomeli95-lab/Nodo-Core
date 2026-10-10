# NODO CORE

**An intelligent personal & business operating layer.** NODO connects intent (voice, text, UI) with memory,
projects, clients, repositories, tasks, agents and tools, and answers as a capable collaborator who actually
knows what is going on.

NODO is **not** a chatbot and **not** an AI wrapper:

| Chatbot | NODO CORE |
|---|---|
| Prompt → model → text | Intent → Context Engine → Orchestrator plan → memory / agents / tools → Model Router → grounded answer |
| Model is the product | Models are interchangeable compute (`ModelProvider`); OpenAI/Anthropic/Ollama are resources, not NODO |
| Memory = chat history in a vector DB | Explicit entities + facts with provenance, confidence, temporal validity, conflict detection |
| "3 agents are thinking…" animation | Every agent run, tool call and inference is a persisted, inspectable record |
| Silent API spend | `NODO_MODE=FREE` never incurs paid usage; every inference is metered |

Status: **V0.2 — foundation + night shift** (see [Current capabilities](#current-capabilities)).

> **Per iniziare su Windows:** [COME-INIZIARE.md](COME-INIZIARE.md) — `demo-nodo.bat` per la demo,
> `avvia-nodo.bat` per l'uso reale, accessi in [docs/ACCESSI.md](docs/ACCESSI.md).

## Night shift (turno di notte)

While you sleep NODO watches every Vercel site (deploys, pages with a real browser, runtime errors, certificate,
Dependabot, CI), rolls back a deploy that took production down, rebuilds builds that failed on transient errors,
prepares a fix as a branch + PR with a tested Vercel preview, writes drafts of planned content, and in the
morning tells you what happened ("cosa hai fatto stanotte?"), in the dashboard, by voice or on Telegram.
Publishing a fix always waits for your approval. Details: [docs/NIGHT_SHIFT.md](docs/NIGHT_SHIFT.md) ·
decision record: [ADR-009](docs/adr/ADR-009-night-shift.md).

## Architecture in one picture

```
USER ── voice / text / UI
          │  POST /api/v1/command  (SSE stream)
          ▼
   ┌──────────────── NODO CORE (modular monolith, FastAPI) ────────────────┐
   │ Intent Engine ─► Context Engine ─► Orchestrator (explicit plan)       │
   │                                      │       │        │               │
   │                                   Memory   Agents    Tools            │
   │                                 (facts,   (PM, DEV, (GitHub…          │
   │                                  graph)    CONTENT)  + permissions)   │
   │                                      └───────┴────────┘               │
   │                                     Model Router (mode, privacy, cost)│
   │                     deterministic │ ollama │ groq │ openrouter │ openai│
   │ Events (append-only) · Telemetry (inference/agent/tool runs)          │
   └────────────────────────────────────────────────────────────────────────┘
          │
   SQLite (dev) / PostgreSQL (prod) via SQLAlchemy 2 + Alembic
```

Full description: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Decisions: [docs/adr/](docs/adr/) —
ADR-001 architecture · 002 providers · 003 memory · 004 agents · 005 voice · 006 security · 007 persistence ·
008 local/cloud.

## Repository layout

```
apps/api/        Python 3.12 · FastAPI · the Core (nodo/) + tests + evals + alembic
  nodo/core/     intent, context, orchestrator, permissions, events, telemetry
  nodo/providers model provider abstraction + router + implementations
  nodo/memory/   facts, provenance, relationships, entity resolution
  nodo/agents/   agent framework (budgets) + PM / DEV / CONTENT
  nodo/tools/    Tool API + GitHub connector (http + fake)
  nodo/voice/    STT/TTS abstractions, voice session state machine
  nodo/api/      REST + SSE + voice routes
apps/web/        React + TypeScript + Vite command center (streaming, push-to-talk)
docs/            VISION, ARCHITECTURE, ROADMAP, SECURITY, VOICE, MEMORY, AGENTS, PROVIDERS, LOCAL_NODE, adr/
.github/         CI (pytest + ruff + alembic + web build)
```

## Run locally

Requirements: Python ≥ 3.12, Node ≥ 20. No API keys needed (FREE mode, deterministic provider, browser voice).

```bash
# API
cd apps/api
pip install -e ".[dev]"
cp ../../.env.example .env            # optional; defaults work
python -m nodo.db.migrate             # creates/upgrades ./nodo.db (safe with create_all-made databases)
python -m nodo.seed                   # optional: fictional demo workspace
uvicorn nodo.main:app --reload        # http://127.0.0.1:8000  (docs at /docs)

# Web (second terminal)
cd apps/web
npm install && npm run dev            # http://localhost:5173 (proxies /api to :8000)
```

Try (text or hold the mic / Space bar):

```
Nodo, mostrami i miei progetti.
Nodo, fammi il punto su Prosperya.
PB CARe.                      ← conversational reference, previous intent re-applied
Cosa manca?                   ← active context (PB CARe) reused
Nodo, cosa devo fare oggi?
Nodo, prepara il prossimo contenuto per SoftComfort.
Nodo, controlla lo stato tecnico di Nodo Core.
```

Without a language model configured, NODO answers with the **grounded summary it computed from stored data**
(provider `deterministic`). It never invents. Configure a provider to get natural phrasing.

## Configure providers

Everything is environment-driven (`NODO_*`, see [.env.example](.env.example)). A provider is registered only
if its variables exist; the router then chooses per request according to mode, data sensitivity and
capabilities. Details and the privacy matrix: [docs/PROVIDERS.md](docs/PROVIDERS.md).

| Provider | Variables | Tier | Max data class |
|---|---|---|---|
| Ollama (local) | `NODO_OLLAMA_BASE_URL`, `NODO_OLLAMA_MODEL` | local | HIGHLY_SENSITIVE |
| Groq | `NODO_GROQ_API_KEY` | free | INTERNAL |
| OpenRouter (`:free` models) | `NODO_OPENROUTER_API_KEY` | free | INTERNAL |
| OpenAI | `NODO_OPENAI_API_KEY` | paid | CONFIDENTIAL |
| deterministic | always on | local | HIGHLY_SENSITIVE |

Modes: `FREE` (default; never pays), `BALANCED`, `PERFORMANCE`, `PRIVATE` (local only).

## Tests and evaluations

```bash
cd apps/api
pytest -q                 # 85 tests: router, permissions, memory, orchestrator, agents, API, voice, night shift
pytest -q evals           # AI behaviour scenarios (evals/scenarios/v01.json), deterministic & free
ruff check nodo tests evals
cd ../web && npm run build
```

All external systems have deterministic doubles: `FakeModelProvider`, `FakeGitHubConnector`, `FakeSTT/FakeTTS`.

## Current capabilities

- Entities: organizations, users, clients, projects, repositories, tasks, decisions, documents, content items,
  memory facts (provenance, confidence, status, validity window, supersession, conflict detection), relationships.
- Command surface: `POST /api/v1/command` streams `intent → plan → context → step/agent/tool events → token → done`.
- Intent Engine (rule-based IT/EN) with entity resolution (aliases, fuzzy) and conversational references.
- Context Engine producing an inspectable `ContextPackage` with max sensitivity.
- Orchestrator with explicit plans; agents **PM**, **DEV** (GitHub snapshot, persisted), **CONTENT** (grounded
  draft); agent budgets (time/model/tool/depth) and crash isolation.
- Model Router: FREE/BALANCED/PERFORMANCE/PRIVATE, sensitivity gate, fallback chain, per-call telemetry.
- Permissions: action levels 0–4; levels ≥3 create an `Approval` instead of executing.
- Voice (Phase 2 realtime): WebSocket `/voice/stream` with typed protocol, `VoiceRuntime` over the same
  Core — partial/final transcripts, token+sentence streaming, sentence-level TTS, real barge-in, bound
  voice approvals, per-turn latency metrics. Push-to-talk UX; browser (free/on-device) or OpenAI speech.
- Telemetry: `/telemetry/usage`, `/telemetry/trace/{request_id}`, `/timeline` (append-only event log).
- Single-user token auth (`NODO_API_TOKEN`), CORS, migrations, CI.
- Night shift: Vercel + GitHub (write) connectors, HTTP/browser probes, incidents, rollback/redeploy, fix agent
  with PR + preview check, executed approvals, morning briefing (dashboard, voice, Telegram), in-process
  scheduler, Anthropic provider, DEMO world, Windows launcher and doctor (`python -m nodo.doctor`).

## Known limitations (V0.1)

- Natural phrasing requires a configured LLM; the deterministic provider returns structured summaries.
- Intent classification is rule-based; a model-backed classifier plugs into `IntentEngine.classify`.
- Voice realtime transport is in place but real streaming STT/TTS providers are still fakes/browser-only;
  no wake word, no VAD-based hands-free turn detection (Phase 5 local Node).
- GitHub connector is read-only and un-cached beyond the stored `tech_state` snapshot.
- Memory retrieval is relational (no embeddings yet); pgvector is an additive step.
- Single workspace; multi-tenant fields exist, RBAC does not.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md). Next: Phase 2 (streaming voice over WebSocket) and Phase 4
(approval-executed write connectors), in parallel with a model-backed intent classifier.
