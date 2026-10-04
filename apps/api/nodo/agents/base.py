"""Agent framework (ADR-004). An agent is a specialized, budgeted unit of work over a ContextPackage.

V0.1 agents compute structured findings from real data (and tools). They do not free-generate text: the single
language-synthesis step happens in the orchestrator so FREE mode costs at most one inference per request.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from nodo.core.context import ContextPackage
from nodo.core.events import EventBus
from nodo.db.models import AgentRun
from nodo.providers.router import ModelRouter
from nodo.tools.base import ToolExecutor, ToolResult


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    max_seconds: float = 60
    max_model_calls: int = 4
    max_tool_calls: int = 8
    max_child_agents: int = 2
    max_depth: int = 2
    model_calls: int = 0
    tool_calls: int = 0
    child_agents: int = 0
    started: float = field(default_factory=time.perf_counter)

    def charge(self, kind: str) -> None:
        if time.perf_counter() - self.started > self.max_seconds:
            raise BudgetExceeded("time budget exceeded")
        n = getattr(self, kind) + 1
        setattr(self, kind, n)
        if n > getattr(self, f"max_{kind}"):
            raise BudgetExceeded(f"{kind} budget exceeded ({n} > {getattr(self, f'max_{kind}')})")


@dataclass
class AgentReport:
    agent: str
    summary_lines: list[str] = field(default_factory=list)  # grounded, human-readable, in the user's language
    findings: dict[str, Any] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: str = "known"  # known | inferred | unknown
    freshness_notes: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    request_id: str
    package: ContextPackage
    session: Session
    tools: ToolExecutor
    router: ModelRouter
    events: EventBus
    budget: Budget
    run_id: str = ""
    depth: int = 0
    language: str = "it"

    async def tool(self, tool: str, action: str, **params) -> ToolResult:
        self.budget.charge("tool_calls")
        return await self.tools.run(tool, action, params, request_id=self.request_id, agent_run_id=self.run_id)


class Agent(Protocol):
    name: str
    description: str

    async def run(self, ctx: AgentContext) -> AgentReport: ...


class AgentRunner:
    def __init__(self, session: Session, events: EventBus):
        self.s, self.events = session, events

    async def run(self, agent: Agent, ctx: AgentContext) -> AgentReport:
        if ctx.depth > ctx.budget.max_depth:
            raise BudgetExceeded("agent recursion depth exceeded")
        run = AgentRun(request_id=ctx.request_id, agent_name=agent.name, budget=_budget_dict(ctx.budget),
                       input={"intent": ctx.package.intent, "focus": ctx.package.focus})
        self.s.add(run)
        self.s.flush()
        ctx.run_id = run.id
        self.events.emit("agent.started", {"agent": agent.name, "run_id": run.id}, request_id=ctx.request_id)
        try:
            report = await agent.run(ctx)
            run.status, run.output = "ok", asdict(report)
        except BudgetExceeded as e:
            run.status, run.error = "budget_exceeded", str(e)
            report = AgentReport(agent.name, [f"{agent.name} interrotto: {e}"], confidence="unknown")
        except Exception as e:  # an agent crash must not crash the session
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
            report = AgentReport(agent.name, [f"{agent.name} non disponibile ({type(e).__name__})."], confidence="unknown")
        run.finished_at = datetime.now(UTC)
        run.usage = {"model_calls": ctx.budget.model_calls, "tool_calls": ctx.budget.tool_calls,
                     "seconds": round(time.perf_counter() - ctx.budget.started, 3)}
        self.s.flush()
        self.events.emit("agent.finished", {"agent": agent.name, "run_id": run.id, "status": run.status},
                         request_id=ctx.request_id)
        return report


def _budget_dict(b: Budget) -> dict:
    return {k: getattr(b, k) for k in ("max_seconds", "max_model_calls", "max_tool_calls", "max_child_agents", "max_depth")}
