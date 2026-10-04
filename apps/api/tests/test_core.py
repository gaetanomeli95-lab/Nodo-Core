"""Orchestrator / context / intent / memory / permissions / agents, all deterministic."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from nodo.core.permissions import ActionLevel, PermissionPolicy
from nodo.db.models import AgentRun, Approval, ContentItem, FactStatus, InferenceRun, Project, ToolExecution
from nodo.memory.service import MemoryService
from nodo.tools.github import RepoSnapshot


async def test_list_projects(core):
    out = await core.run_once("Nodo, mostrami i miei progetti.")
    assert out.intent == "list_projects"
    assert "Prosperya CRM" in out.text and "5 progetti" in out.text
    assert out.provider == "deterministic"


async def test_project_status_runs_pm_and_dev(core, db, github):
    out = await core.run_once("Fammi il punto su Prosperya.")
    assert out.intent == "project_status"
    assert "Prosperya CRM" in out.text and "decisioni aperte" in out.text
    runs = db.scalars(select(AgentRun).where(AgentRun.request_id == out.request_id)).all()
    assert {r.agent_name for r in runs} == {"pm", "dev"} and all(r.status == "ok" for r in runs)
    assert github.calls == ["studio-nodo-demo/prosperya-crm"]
    tools = db.scalars(select(ToolExecution).where(ToolExecution.request_id == out.request_id)).all()
    assert tools and tools[0].level == ActionLevel.READ and tools[0].status == "ok"
    inf = db.scalars(select(InferenceRun).where(InferenceRun.request_id == out.request_id)).all()
    assert len(inf) == 1 and inf[0].estimated_cost == 0 and inf[0].is_local


async def test_conversational_reference(core):
    first = await core.run_once("Fammi il punto su Prosperya.")
    second = await core.run_once("PB CARe.", conversation_id=first.conversation_id)
    assert second.intent == "project_status" and "PB CARe" in second.text
    third = await core.run_once("Cosa manca?", conversation_id=first.conversation_id)
    # "cosa manca" has no entity -> active context (PB CARe) must be reused
    assert "PB CARe" in third.text


async def test_today_ranks_overdue_and_blocked(core):
    out = await core.run_once("Nodo, cosa devo fare oggi?")
    assert out.intent == "today"
    assert "Risolvere errore deploy staging" in out.text  # overdue + blocked + priority 1


async def test_prepare_content_creates_draft(core, db):
    out = await core.run_once("Prepara il prossimo contenuto per SoftComfort.")
    assert out.intent == "prepare_content" and "narrativo" in out.text
    drafts = db.scalars(select(ContentItem).where(ContentItem.status == "draft")).all()
    assert len(drafts) == 1 and drafts[0].angle == "narrative" and drafts[0].generated_by_run_id


async def test_tech_status_uses_github_and_persists_snapshot(core, db, github):
    github.snapshots["studio-nodo-demo/pbcare-platform"] = RepoSnapshot(
        "studio-nodo-demo", "pbcare-platform", "main", datetime.now(UTC).isoformat(), commits_last_7d=0,
        open_prs=2, open_issues=1, ci_status="failure")
    out = await core.run_once("Controlla lo stato tecnico di PB CARe.")
    assert out.intent == "tech_status" and "CI failure" in out.text and "CI in errore" in out.text
    p = db.scalars(select(Project).where(Project.name == "PB CARe Piattaforma")).one()
    from nodo.db.models import Repository
    repo = db.scalars(select(Repository).where(Repository.project_id == p.id)).one()
    assert repo.tech_state["ci_status"] == "failure" and repo.last_synced_at


async def test_github_unavailable_degrades_honestly(core, github):
    github.fail = True
    out = await core.run_once("Controlla lo stato tecnico di Prosperya.")
    assert "non disponibile" in out.text or "non aggiornati" in out.text


async def test_unknown_entity_asks_for_clarification(core, db):
    out = await core.run_once("Fammi il punto su Zorbatron.")
    assert "Non ho capito" in out.text
    assert not db.scalars(select(InferenceRun)).all()  # no inference spent on an unresolved request


async def test_stop_intent(core):
    out = await core.run_once("No, fermati.")
    assert out.intent == "stop"


async def test_sensitivity_propagates_to_router(core):
    await core.run_once("Fammi il punto su Prosperya.")   # CLIENT_PRIVATE project
    assert str(core.router.last_decision.descriptor.max_sensitivity) in ("HIGHLY_SENSITIVE",)


def test_permission_levels():
    p = PermissionPolicy()
    assert not p.evaluate("github", "snapshot", ActionLevel.READ).requires_approval
    assert not p.evaluate("content", "draft", ActionLevel.PREPARE).requires_approval
    assert p.evaluate("github", "create_issue", ActionLevel.EXECUTE_REVERSIBLE).requires_approval
    assert p.evaluate("gmail", "send", ActionLevel.EXECUTE_SENSITIVE).requires_approval
    relaxed = PermissionPolicy(auto_approve_reversible=True, auto_approve_sensitive={"gmail.send"})
    assert not relaxed.evaluate("github", "create_issue", ActionLevel.EXECUTE_REVERSIBLE).requires_approval
    assert not relaxed.evaluate("gmail", "send", ActionLevel.EXECUTE_SENSITIVE).requires_approval
    assert relaxed.evaluate("gmail", "delete_all", ActionLevel.EXECUTE_SENSITIVE).requires_approval
    assert not PermissionPolicy(denied_actions={"github.snapshot"}).evaluate("github", "snapshot", ActionLevel.READ).allowed


async def test_level3_action_creates_approval_instead_of_executing(core, db):
    res = await core.tools.run("github", "create_issue", {"owner": "o", "name": "n", "title": "t"}, request_id="r1")
    assert res.status == "pending_approval" and res.approval_id
    a = db.get(Approval, res.approval_id)
    assert a.status == "pending" and a.level == 3


def test_memory_conflict_and_supersession(db, seeded):
    mem = MemoryService(db, seeded)
    f1 = mem.remember("client", "c1", "Prezzo listino: 100", predicate="price")
    f2 = mem.remember("client", "c1", "Prezzo listino: 120", predicate="price")
    assert f1.status == FactStatus.CONFLICTING and f2.conflicts_with == f1.id
    f3 = mem.supersede(f1, "Prezzo listino: 130", source_type="conversation")
    assert f1.status == FactStatus.SUPERSEDED and f1.superseded_by == f3.id and f1.valid_until
    current = mem.facts_for("client", "c1")
    assert f1.id not in {f.id for f in current} and f3.id in {f.id for f in current}


def test_entity_resolver_aliases_and_fuzzy(core):
    assert core.resolver.best("fammi il punto su pb care").name == "PB CARe"
    assert core.resolver.best("prosperia").name in ("Prosperya", "Prosperya CRM")
    assert core.resolver.best("soft comfort").name == "SoftComfort"
    assert core.resolver.best("qualcosa di totalmente diverso") is None


async def test_agent_budget_is_enforced(core):
    from nodo.agents.base import AgentContext, AgentReport, Budget, BudgetExceeded

    class Greedy:
        name, description = "greedy", "calls too many tools"

        async def run(self, ctx):
            for _ in range(5):
                await ctx.tool("github", "snapshot", owner="o", name="n")
            return AgentReport(self.name)

    from nodo.core.context import ContextPackage
    ctx = AgentContext("req", ContextPackage("x", core.org), core.s, core.tools, core.router, core.events,
                       Budget(max_tool_calls=2))
    rep = await core.runner.run(Greedy(), ctx)
    assert rep.confidence == "unknown" and "budget" in rep.summary_lines[0]
    with pytest.raises(BudgetExceeded):
        Budget(max_model_calls=0).charge("model_calls")
