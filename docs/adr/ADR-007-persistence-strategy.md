# ADR-007 — Persistence strategy: one relational model, SQLite → PostgreSQL, append-only event log

**Status:** accepted · **Date:** 2026-10-04

## Context
NODO must run on a laptop with zero setup, be fully testable in-process, and later run as a cloud Core with
local Nodes. It stores domain entities, provenance-rich facts, conversation state and dense telemetry
(inference/agent/tool runs, events). The vision document lists graph and vector stores; adopting them in V0.1
would add operations without a demonstrated query need.

## Decision
1. **One relational schema** declared with SQLAlchemy 2 typed mapped classes (`nodo/db/models.py`), migrated
   with Alembic. Development/test: **SQLite** (`:memory:` in tests, `./nodo.db` locally); production:
   **PostgreSQL** (`postgresql+psycopg://`). Same models, no dialect-specific features.
2. **Portable column choices:** UUID stored as `String(36)`; JSON columns for flexible payloads (`tech_state`,
   `budget`, `usage`, `payload`, `active_context`, `aliases`); timezone-aware UTC `DateTime`.
3. **Ownership everywhere:** `organization_id` on every owned entity (multi-tenant boundary in the schema even
   though V0.1 is single-workspace).
4. **Append-only `event_log`.** Application code never updates or deletes rows; it is the audit trail and the
   future job/automation trigger source.
5. **Telemetry is relational, not log files.** `InferenceRun`, `AgentRun`, `ToolExecution` are queryable rows
   tied together by `request_id`.
6. **Migrations run in batch mode** so SQLite can apply `ALTER` operations; CI runs `alembic upgrade head`.
7. **Facts are never deleted** (ADR-003): supersession and `valid_until` instead.
8. **Session handling:** short-lived `Session` per request (`session_scope()`), `expire_on_commit=False`,
   foreign keys enforced on SQLite via PRAGMA to match PostgreSQL behaviour.

## Alternatives considered
- **PostgreSQL only.** Rejected for V0.1: zero-setup local run and in-memory tests matter more now.
- **Document store (Mongo) / JSON-everything.** Rejected: provenance, temporal validity and joins are
  inherently relational.
- **Neo4j for the relationship graph.** Deferred (ADR-003); `Relationship` rows suffice.
- **Dedicated vector DB.** Deferred; pgvector is an additive column/index on PostgreSQL when embeddings arrive.
- **Redis/Kafka for events.** Deferred; `event_log` + in-process `EventBus.subscribe` is the seam.

## Consequences
+ `pip install && alembic upgrade head` is the whole setup; tests are fast and deterministic.
+ Switching to PostgreSQL is configuration (`NODO_DATABASE_URL`).
− SQLite JSON columns are opaque to queries; some filters happen in Python until PostgreSQL.
− `event_log` and telemetry tables grow unbounded; retention/archival is a future job.
− In-memory `VoiceSessionStore` is process-local (not persisted); fine for one process, not for scale-out.

## Current V0.1 implementation (implemented)
`nodo/db/models.py` (18 tables), `nodo/db/session.py`, `alembic/` with initial revision
`51ea0000e744_initial_schema`, CI step `alembic upgrade head`, `python -m nodo.seed` (idempotent demo data).

## Architecturally prepared (seams exist, no implementation)
- `Document.uri/summary` for ingestion; `MemoryFact.sensitivity` for storage partitioning.
- `EventLog` as trigger source for a job runner.

## Future evolution (planned)
pgvector for embeddings; retention/archival jobs; `storage_location` for HIGHLY_SENSITIVE facts on local
Nodes (ADR-008); persisted voice sessions/turns with latency metrics (ADR-005 Phase 2M); read replicas only
if telemetry volume requires it.
