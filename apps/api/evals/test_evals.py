"""Lightweight AI behaviour evaluations. Scenarios live in scenarios/*.json and run against the seeded demo
workspace with deterministic adapters, so they are reproducible and free. The same scenarios can later be run
against real providers (set NODO_EVAL_LIVE=1) to measure regression of natural-language quality."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from nodo.db.models import SENSITIVITY_RANK, AgentRun, InferenceRun, Sensitivity, ToolExecution

SCENARIOS = json.loads((Path(__file__).parent / "scenarios" / "v01.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
async def test_scenario(sc, core, db):
    exp = sc["expect"]
    if "tool" in sc:
        res = await core.tools.run(sc["tool"]["name"], sc["tool"]["action"], sc["tool"]["params"], request_id="eval")
        assert res.status == exp["tool_status"]
        return
    out, conv, last_events = None, None, []
    for turn in sc["turns"]:
        last_events = [ev async for ev in core.handle(turn, conv)]
        out = next(e for e in last_events if e["type"] == "done")
        conv = out["conversation_id"]
    text = out["text"]
    if "intent" in exp:
        assert out["intent"] == exp["intent"], text
    for s in exp.get("contains", []):
        assert s in text, f"expected {s!r} in: {text}"
    for s in exp.get("not_contains", []):
        assert s not in text, text
    if "agents" in exp:
        assert sorted(out["agents"]) == sorted(exp["agents"])
    if exp.get("entity_from_context"):
        intent_ev = next(e for e in last_events if e["type"] == "intent")
        assert intent_ev["from_context"] or intent_ev["reference_reapplied"]
    inf = db.scalars(select(InferenceRun).where(InferenceRun.request_id == out["request_id"])).all()
    if "max_inference_calls" in exp:
        assert len(inf) <= exp["max_inference_calls"]
    if "paid_calls" in exp:
        assert sum(1 for i in inf if i.estimated_cost > 0) == exp["paid_calls"]
    if "provider_in" in exp:
        assert out["provider"] in exp["provider_in"]
    if "tool_calls" in exp:
        tools = db.scalars(select(ToolExecution).where(ToolExecution.request_id == out["request_id"])).all()
        assert [f"{t.tool_name}.{t.action}" for t in tools] == exp["tool_calls"]
    if "min_provider_sensitivity" in exp:
        d = core.router.last_decision.descriptor
        assert SENSITIVITY_RANK[Sensitivity(d.max_sensitivity)] >= SENSITIVITY_RANK[Sensitivity(exp["min_provider_sensitivity"])]
    runs = db.scalars(select(AgentRun).where(AgentRun.request_id == out["request_id"])).all()
    assert all(r.status in ("ok",) for r in runs), [r.error for r in runs]
