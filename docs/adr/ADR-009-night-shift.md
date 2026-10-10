# ADR-009 — Night shift: autonomous monitoring, reversible self-healing, approved fixes

**Status:** accepted · **Date:** 2026-10-09

## Context
The owner runs many small production sites (GitHub repo + Vercel project each) and wants NODO to work while he
sleeps: notice what broke, fix what is safe to fix, prepare the rest, and report in the morning. This is the
first time NODO acts on external systems without a human in the loop, so the autonomy must be bounded by code,
not by model behaviour.

## Decision
1. **In-process scheduler, state in the database.** `NightScheduler` runs inside the API process; due jobs are
   derived from `NightRun` rows by a pure `decide()` function (catch-up after downtime, one briefing per day,
   no stale "good morning" in the afternoon). No OS cron, works the same on Windows.
2. **Observe → evaluate → respond.** Network observation runs concurrently and writes nothing; evaluation and
   remediation are sequential, so a single SQLAlchemy session stays safe. Checks are pure functions of the
   observation.
3. **Incidents, not alerts.** Signals are deduplicated by fingerprint (`site:kind:key`), resolve themselves when
   the owning check is healthy, and every remediation is attempted at most once per incident and key.
4. **Reversible actions may be pre-approved; production code changes never are.** Rollback, redeploy and
   opening a fix PR are level 3 and auto-approved through `PermissionPolicy.auto_approve_actions` from
   configuration (globally or per site). Merging is level 4 and always creates an `Approval`. Approvals now
   **execute** the stored action (Phase 4 delivered), from REST or voice.
5. **Known-good tracking drives rollback.** A deployment becomes `last_good` only when production answered
   healthily. Rollback targets it; without history, only a deploy younger than 48h may be rolled back to its
   predecessor; if production *is* the last good deployment, the problem is not the deploy and NODO escalates.
6. **Fix agent with hard rails.** Files are chosen by NODO (named in errors, changed since last good, or
   config); the model may rewrite only those, never secrets, CI or lockfiles; ≤ 300 changed lines; truncated
   output rejected; one commit on `nodo/fix-*`, one PR, the Vercel preview probed before asking for approval.
7. **Spending is explicit and capped.** Fixes may use a paid coding model even in FREE mode only via
   `NODO_NIGHT_FIX_ALLOW_PAID` (default on, documented) and within `NODO_NIGHT_MAX_COST_USD` per night. This
   is a deliberate, scoped exception to ADR-006 §5; conversation stays governed by the normal FREE rules.
8. **Grounded briefing.** The morning briefing is composed deterministically from recorded rows; a model may
   only rephrase it in conversation.
9. **Offline world for tests and demo.** `nodo/night/simulation.py` couples fake Vercel, fake GitHub, a prober
   reading fake health and a scripted coder, so the whole loop is tested end to end and demoable without access.

## Consequences
+ The owner gets value with zero configuration (DEMO), then with read-only tokens (monitoring and briefing),
  then with write tokens (self-healing and fixes), each step strictly additive.
+ Every action is a `ToolExecution` + `NightAction`, so "what did you do?" always has an exact answer.
− Runtime-log access through the REST stream is best effort; deeper log analytics need a Log Drain (future).
− After a Vercel Instant Rollback, auto-assignment of production domains stops; NODO promotes the deployment
  of an approved fix to restore it, and the owner should know this behaviour.
