"""Telemetry: persists inference runs and exposes usage aggregates (Energy / Cost Manager foundations)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nodo.db.models import AgentRun, InferenceRun, ToolExecution
from nodo.providers.router import InferenceRecord


class Telemetry:
    def __init__(self, session: Session):
        self.session = session

    def record_inference(self, r: InferenceRecord) -> None:
        self.session.add(InferenceRun(request_id=r.request_id or "-", agent_run_id=r.agent_run_id,
                                      provider=r.provider, model=r.model, purpose=r.purpose, is_local=r.is_local,
                                      tokens_in=r.tokens_in, tokens_out=r.tokens_out, latency_ms=r.latency_ms,
                                      estimated_cost=r.estimated_cost, status=r.status, error=r.error))
        self.session.flush()

    def usage_today(self) -> dict:
        since = datetime.now(UTC) - timedelta(days=1)
        s = self.session
        rows = s.execute(select(InferenceRun.provider, InferenceRun.is_local, func.count(), func.sum(InferenceRun.tokens_in),
                                func.sum(InferenceRun.tokens_out), func.sum(InferenceRun.estimated_cost))
                         .where(InferenceRun.created_at >= since).group_by(InferenceRun.provider, InferenceRun.is_local)).all()
        by_provider = [{"provider": p, "is_local": loc, "calls": c, "tokens_in": ti or 0, "tokens_out": to or 0,
                        "estimated_cost": round(cost or 0.0, 6)} for p, loc, c, ti, to, cost in rows]
        local = sum(r["calls"] for r in by_provider if r["is_local"])
        cloud = sum(r["calls"] for r in by_provider if not r["is_local"])
        cost = round(sum(r["estimated_cost"] for r in by_provider), 6)
        agent_runs = s.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.started_at >= since)) or 0
        tool_execs = s.scalar(select(func.count()).select_from(ToolExecution).where(ToolExecution.created_at >= since)) or 0
        return {"local_requests": local, "cloud_requests": cloud, "premium_requests": sum(
            r["calls"] for r in by_provider if r["estimated_cost"] > 0), "agent_runs": agent_runs,
            "tool_executions": tool_execs, "tokens": sum(r["tokens_in"] + r["tokens_out"] for r in by_provider),
            "estimated_cost": cost, "by_provider": by_provider}

    def trace(self, request_id: str) -> dict:
        s = self.session
        return {
            "request_id": request_id,
            "inference": [_row(r) for r in s.scalars(select(InferenceRun).where(InferenceRun.request_id == request_id))],
            "agents": [_row(r) for r in s.scalars(select(AgentRun).where(AgentRun.request_id == request_id))],
            "tools": [_row(r) for r in s.scalars(select(ToolExecution).where(ToolExecution.request_id == request_id))],
        }


def _row(obj) -> dict:
    return {c.name: (getattr(obj, c.name).isoformat() if isinstance(getattr(obj, c.name), datetime) else getattr(obj, c.name))
            for c in obj.__table__.columns}
