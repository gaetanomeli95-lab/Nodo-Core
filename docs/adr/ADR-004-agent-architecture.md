# ADR-004 — Agent architecture: specialised, budgeted, data-grounded, inspectable

**Status:** accepted · **Date:** 2026-10-04

## Context
"Multi-agent" is frequently theatre: several prompts talk to each other, consuming tokens and time, with no
guarantee the result is better than one well-grounded call. NODO needs agents only where specialisation
genuinely improves results (project management reasoning, technical state of a repository, content planning),
and it needs every agent run to be bounded, observable and safe to fail.

## Decision
1. **An agent exists only when specialisation improves results.** V0.1 ships three: PM, DEV, CONTENT.
2. **Agents reason over structured data, not over free text.** They receive a `ContextPackage` and return an
   `AgentReport` (`summary_lines`, `findings`, `risks`, `recommendations`, `confidence ∈ {known, inferred,
   unknown}`, `freshness_notes`). The single language step is the orchestrator's synthesis. FREE mode therefore
   costs ≤ 1 inference per request.
3. **Explicit plans, streamed before execution.** `PLANS[intent]` lists steps (`resolve_entity`, `agent:pm`,
   `agent:dev?` …). The user sees what NODO is about to do; nothing is improvised by a model.
4. **Hard budgets per run.** `Budget(max_seconds, max_model_calls, max_tool_calls, max_child_agents, max_depth)`;
   `charge()` raises `BudgetExceeded`, recorded as `status=budget_exceeded`.
5. **Crash isolation.** `AgentRunner` wraps every run; a failing agent yields a report with
   `confidence=unknown` and the request continues. No agent failure fails the user's turn.
6. **Every run is a persisted record.** `AgentRun` rows (budget, usage, status, output, error) plus
   `agent.started/finished` events; tool calls go through `ToolExecutor` and are `ToolExecution` rows.
7. **Tools are reached only through the Tool API** (ADR-006), so permission levels apply to agents exactly as
   to the orchestrator.

## Alternatives considered
- **Single monolithic prompt with tool-calling.** Simpler, but opaque, expensive to test deterministically,
  and incompatible with FREE mode (requires a capable model for every turn).
- **Generic agent framework (LangGraph/AutoGen-style).** Rejected: heavy dependency, model-centric control
  flow, hard to keep FREE/deterministic; our plan+budget model is ~200 lines and fully testable.
- **LLM-planned dynamic agent graphs.** Deferred: child budgets and `depth` are modelled so dynamic sub-agents
  can be added without redesign, but no use case justifies it yet.

## Consequences
+ Unit-testable agents without any model; deterministic evals.
+ Predictable cost and latency; honest `confidence` and `freshness_notes` surface uncertainty to the user.
+ The UI shows real runs, not animations.
− Agents cannot read unstructured documents yet (requires ingestion → facts first).
− Adding an intent means adding a plan and possibly an agent; there is no "ask the model to figure it out".

## Current V0.1 implementation (implemented)
`nodo/agents/base.py` (`Agent`, `AgentContext`, `Budget`, `AgentReport`, `AgentRunner`), `pm.py`, `dev.py`,
`content.py`; `PLANS` in `nodo/core/orchestrator.py`. Tests: `test_project_status_runs_pm_and_dev`,
`test_tech_status_uses_github_and_persists_snapshot`, `test_github_unavailable_degrades_honestly`,
`test_prepare_content_creates_draft`, `test_agent_budget_is_enforced`.

## Architecturally prepared (seams exist, no implementation)
- `AgentRun.parent_run_id`, `Budget.max_child_agents`, `AgentContext.depth` for sub-agents.
- `MemoryFact.layer="agent"` for agent memory.
- `AgentContext.router` lets an agent call a model within its budget (unused in V0.1 agents).

## Future evolution (planned)
RESEARCH, MEMORY (extraction/dedupe/conflicts), OPS, GUARD (policy checks before execution) agents; dynamic
sub-agents; agent-level memory; model-backed intent classifier feeding the same plans.
