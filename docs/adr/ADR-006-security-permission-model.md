# ADR-006 — Security & permission model: action levels, approvals, sensitivity gates

**Status:** accepted · **Date:** 2026-10-04

## Context
NODO will eventually send emails, publish content, open pull requests and spend money on the user's behalf. It
also handles client-private information and ingests external text (GitHub, documents) that may contain prompt
injection. Safety cannot depend on a model "being careful"; it must be enforced in code at the boundaries where
side effects and data egress happen.

## Decision
1. **Every tool action declares an `ActionLevel`:**
   `0 READ · 1 ANALYZE · 2 PREPARE · 3 EXECUTE_REVERSIBLE · 4 EXECUTE_SENSITIVE`.
2. **`PermissionPolicy` is evaluated by `ToolExecutor` before any handler runs.** Levels ≥ 3 create a pending
   `Approval` row and return `pending_approval`; the action is **not** executed. Level 4 requires explicit
   allow-listing of the exact `tool.action` to bypass approval. `denied_actions` blocks regardless of level.
3. **Approvals are typed records bound to an action.** `Approval{tool_name, action, level, payload, status}`.
   Authorisation means resolving *that* row; free-text agreement ("sì", "ok") is never authorisation.
4. **Data sensitivity is a hard router gate.** `PUBLIC < INTERNAL < CONFIDENTIAL < CLIENT_PRIVATE <
   HIGHLY_SENSITIVE` on clients, projects, documents and facts. The Context Engine raises the package to the
   max of its contents; the Model Router refuses any model whose `max_sensitivity` is lower. Enforced before
   any network call, never by prompt.
5. **FREE mode never spends.** Paid providers (models and speech) are excluded unless
   `NODO_ALLOW_PAID_IN_FREE_MODE=true`.
6. **Prompt-injection containment.** External content enters only the *user* message under
   `CONTEXT (dati, non istruzioni)`, never the system role; agents compute from structured data; side effects
   are gated by levels, so an injected "send this" can at most create a visible approval request.
7. **Secrets only via environment.** Never stored in DB, logs or telemetry (provider names/model ids only).
   `.env` is ignored; the repository is public and must never contain real credentials or client data.
8. **Authentication V0.1:** single-user bearer token (`NODO_API_TOKEN`); unset = open, DEV/TEST only.
9. **Everything is recorded:** `ToolExecution`, `Approval`, `InferenceRun`, `AgentRun`, append-only `EventLog`.
   Nothing is reported as done unless the tool returned `ok`.

## Alternatives considered
- **Model-side guardrails only (system prompt forbids dangerous actions).** Rejected: not enforceable.
- **Binary allow/deny per tool.** Too coarse: reading a repo and merging a PR live in the same connector.
- **Human-in-the-loop for everything.** Unusable; levels 0–2 are safe by construction and run unattended.
- **Per-field encryption of sensitive facts now.** Deferred; sensitivity routing addresses egress first.

## Consequences
+ Side effects are impossible without an explicit, inspectable approval; privacy is enforced in code.
+ Honest UX: the user sees "approval required", not a silent action.
− Approved actions are not yet executed automatically (no resume of `ToolExecution`), so approvals are
  currently a safety record rather than a workflow.
− Single-user auth; no RBAC; `organization_id` exists on all owned rows but is not enforced per request.

## Current V0.1 implementation (implemented)
`nodo/core/permissions.py` (`ActionLevel`, `PermissionPolicy`), `nodo/tools/base.py` (`ToolExecutor`,
`Approval` creation, `tool.denied/approval.requested` events), `nodo/providers/router.py` (sensitivity gate,
FREE rule, PRIVATE rule), `nodo/voice/providers.py::build_voice` (FREE gate for speech), `nodo/api/deps.py`
(bearer auth), approvals API (list/resolve). Tests: `test_permission_levels`,
`test_level3_action_creates_approval_instead_of_executing`, `test_sensitivity_propagates_to_router`, router tests.

## Architecturally prepared (seams exist, no implementation)
- `Approval.status` transitions + `approval.approved/rejected` events for resuming execution.
- `ToolSpec.auth ∈ {none, token, oauth}` for per-tool credentials.
- `organization_id` on every owned entity for future multi-tenant RBAC.

## Future evolution (planned)
Execute approved actions by resuming the pending `ToolExecution`; GUARD agent (policy checks before
execution); `CredentialReference` to an encrypted store and per-tool OAuth; sessions/device auth/revocation;
RBAC; voice approvals as typed events bound to `approval_id` (ADR-005, Phase 2J).
