# Agents

An agent exists only when specialization improves results (ADR-004). No "agent theater": each run is an
`AgentRun` row with budget, usage, status and output; the UI shows exactly those.

## Framework (`nodo/agents/base.py`)
- `Agent.run(ctx: AgentContext) -> AgentReport`
- `AgentContext`: `request_id`, `package` (ContextPackage), `session`, `tools`, `router`, `events`, `budget`, `depth`.
- `Budget`: `max_seconds`, `max_model_calls`, `max_tool_calls`, `max_child_agents`, `max_depth`; `charge()` raises
  `BudgetExceeded`, recorded as `status=budget_exceeded`.
- `AgentReport`: `summary_lines` (grounded, user language), `findings`, `risks`, `recommendations`,
  `confidence` (`known | inferred | unknown`), `freshness_notes`.
- `AgentRunner`: persistence, events, crash isolation (a failing agent never fails the request).

V0.1 agents **reason over data, not over free text**; the single language step is the orchestrator's synthesis.
This keeps FREE mode at ≤1 inference per request and makes agents unit-testable without a model.

## Agents
| Agent | Intent(s) | What it really does |
|---|---|---|
| **PM** | project_status, today | open/in-progress/blocked/overdue tasks per project, open decisions, staleness (≥10 days), next actions |
| **DEV** | tech_status, project_status (if repos) | `github.snapshot` via ToolExecutor (level 0), persists `Repository.tech_state`, flags CI failure / inactivity / open PRs, degrades to cached snapshot with freshness note |
| **CONTENT** | prepare_content | picks the least-covered angle from published history, uses client facts, creates a `ContentItem` draft (level 2) |

Planned: RESEARCH, MEMORY (fact extraction, dedupe, conflicts), OPS, GUARD (policy checks before execution),
dynamic sub-agents (child budgets already modelled).

## Plans
`PLANS` in `orchestrator.py` maps intent → explicit steps (`agent:dev?` = conditional on repositories).
Plans are streamed to the client before execution so the user sees what NODO is about to do.
