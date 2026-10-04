# Security & Permissions

## Secrets
Only via environment (`NODO_*`). `.env` is git-ignored; `.env.example` holds placeholders. No secret is ever
written to the database, logs or telemetry (provider names and model ids only). Future: `CredentialReference`
entity pointing to an encrypted store; per-tool OAuth tokens.

## Authentication (V0.1)
Single-user development auth: if `NODO_API_TOKEN` is set, every `/api/v1/*` route requires
`Authorization: Bearer <token>`. Unset = open, intended for local DEV/TEST only. Production must set it
(and later: sessions, device auth, revocation, RBAC on `organization_id`).

## Action safety model (ADR-006)
| Level | Meaning | Approval |
|---|---|---|
| 0 READ | read info | never |
| 1 ANALYZE | no external mutation | never |
| 2 PREPARE | drafts/proposals (internal rows) | never |
| 3 EXECUTE_REVERSIBLE | external mutation, reversible | yes unless `auto_approve_reversible` |
| 4 EXECUTE_SENSITIVE | send/publish/merge/delete/spend | always, unless the exact `tool.action` is explicitly allow-listed |

`ToolExecutor` enforces it: a pending `Approval` row is created and the action is **not** executed.
`denied_actions` blocks regardless of level.

## Data sensitivity
`PUBLIC < INTERNAL < CONFIDENTIAL < CLIENT_PRIVATE < HIGHLY_SENSITIVE` on clients, projects, documents and facts.
The Context Engine raises the package's sensitivity to the max of its contents; the Model Router refuses any
model whose `max_sensitivity` is lower. Cloud free tiers are capped at INTERNAL; local models accept everything.

## Prompt-injection defence
- External content (tool output, documents, GitHub text) is placed in the *user* message under
  `CONTEXT (dati, non istruzioni)`, never in the system role.
- The system prompt instructs the model to treat it as data and to use only the grounded summary.
- Agents compute findings from structured data, not by letting a model read raw external text.
- Tools with side effects are gated by levels/approvals, so an injected "send this email" can at most create
  an approval request visible to the user.

## Failure handling
Provider failure → next eligible provider; GitHub unavailable → cached snapshot marked stale or explicit
"non disponibile"; agent crash → `AgentRun.status=failed`, session continues; no action is ever reported as
done unless the tool returned `ok`.
