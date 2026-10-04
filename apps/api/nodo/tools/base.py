"""Standardized Tool API. Every tool action declares its ActionLevel; the executor enforces policy and records
ToolExecution rows. External content returned by tools is DATA, never instructions (see docs/SECURITY.md)."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from nodo.core.events import EventBus
from nodo.core.permissions import ActionLevel, PermissionPolicy
from nodo.db.models import Approval, ToolExecution


@dataclass
class ToolAction:
    name: str
    level: ActionLevel
    description: str
    handler: Callable[..., Awaitable[dict]]
    input_schema: dict = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    actions: dict[str, ToolAction]
    auth: str = "none"  # none | token | oauth

    def action(self, name: str) -> ToolAction:
        return self.actions[name]


@dataclass
class ToolResult:
    status: str  # ok | failed | denied | pending_approval
    output: dict = field(default_factory=dict)
    error: str | None = None
    execution_id: str | None = None
    approval_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ToolExecutor:
    def __init__(self, session: Session, events: EventBus, policy: PermissionPolicy, organization_id: str):
        self.s, self.events, self.policy, self.org = session, events, policy, organization_id
        self.tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    async def run(self, tool: str, action: str, params: dict[str, Any], *, request_id: str,
                  agent_run_id: str | None = None, reason: str | None = None) -> ToolResult:
        spec = self.tools[tool]
        act = spec.action(action)
        verdict = self.policy.evaluate(tool, action, act.level)
        ex = ToolExecution(request_id=request_id, agent_run_id=agent_run_id, tool_name=tool, action=action,
                           level=int(act.level), input=params)
        self.s.add(ex)
        if not verdict.allowed:
            ex.status, ex.error = "denied", verdict.reason
            self.s.flush()
            self.events.emit("tool.denied", {"tool": tool, "action": action, "reason": verdict.reason}, request_id=request_id)
            return ToolResult("denied", error=verdict.reason, execution_id=ex.id)
        if verdict.requires_approval:
            appr = Approval(organization_id=self.org, request_id=request_id, tool_name=tool, action=action,
                            level=int(act.level), payload=params, reason=reason or verdict.reason)
            self.s.add(appr)
            ex.status, ex.error = "pending_approval", verdict.reason
            self.s.flush()
            self.events.emit("approval.requested", {"approval_id": appr.id, "tool": tool, "action": action,
                                                    "level": int(act.level)}, request_id=request_id, organization_id=self.org)
            return ToolResult("pending_approval", error=verdict.reason, execution_id=ex.id, approval_id=appr.id)
        self.events.emit("tool.started", {"tool": tool, "action": action, "level": int(act.level)}, request_id=request_id)
        t0 = time.perf_counter()
        try:
            out = await act.handler(**params)
            ex.status, ex.output = "ok", out
            self.events.emit("tool.finished", {"tool": tool, "action": action, "ok": True}, request_id=request_id)
            return ToolResult("ok", out, execution_id=ex.id)
        except Exception as e:  # tools must never crash the session
            ex.status, ex.error = "failed", f"{type(e).__name__}: {e}"
            self.events.emit("tool.finished", {"tool": tool, "action": action, "ok": False, "error": ex.error},
                             request_id=request_id)
            return ToolResult("failed", error=ex.error, execution_id=ex.id)
        finally:
            ex.duration_ms = int((time.perf_counter() - t0) * 1000)
            self.s.flush()
