"""Night shift: discovery, checks, incidents, rollback, redeploy, fix PRs, approvals, briefing, scheduler."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nodo.app import build_container
from nodo.config import Settings
from nodo.db.models import Approval, ContentItem, FixProposal, Incident, NightAction, NightRun, Site
from nodo.night import approvals as A
from nodo.night.briefing import compose_briefing, latest_or_live, tzinfo
from nodo.night.fixer import FixAgent
from nodo.night.notify import FakeNotifier
from nodo.night.scheduler import SchedulerState, decide, window_bounds
from nodo.night.simulation import DEMO_BUG, DEMO_OWNER, SimHealth, build_demo_world
from nodo.providers.deterministic import FakeModelProvider


def make_container(world, settings: Settings | None = None, with_coder: bool = True):
    s = settings or Settings()
    providers = [world.model, FakeModelProvider()] if with_coder else [FakeModelProvider()]
    c = build_container(s, providers=providers, vercel=world.vercel, repo=world.github, prober=world.prober,
                        notifier=FakeNotifier())
    c.night_verify_delay = c.night_retry_delay = c.night_poll_interval = 0
    return c


@pytest.fixture
def world():
    return build_demo_world()


@pytest.fixture
def nc(world, settings):
    return make_container(world, settings)


async def baseline(db, c):
    shift = c.night_shift(db)
    await shift.discover()
    for s in db.scalars(select(Site)):
        if s.name == "pbcare-platform":
            s.key_pages = [{"path": "/contatti", "name": "Contatti"}]
    db.flush()
    return await shift.run_pass("light", "test")


def site(db, name) -> Site:
    return db.scalars(select(Site).where(Site.name == name)).one()


async def drain_background():
    while A._background:
        await asyncio.gather(*list(A._background))


# ------------------------------------------------------------------------------------------------ discovery
async def test_discover_links_sites_to_projects(db, seeded, nc):
    created = await nc.night_shift(db).discover()
    assert sorted(s.name for s in created) == ["cf-materassi-sito", "pbcare-platform", "prosperya-crm"]
    pb = site(db, "pbcare-platform")
    assert pb.url == "https://app.pbcare-demo.it" and pb.repo_owner == DEMO_OWNER and pb.project_id
    assert site(db, "cf-materassi-sito").project_id  # matched by normalized name "CF Materassi Sito"
    assert await nc.night_shift(db).discover() == []  # idempotent


async def test_healthy_baseline(db, seeded, nc):
    run = await baseline(db, nc)
    assert run.status == "done" and run.summary["sites"] == 3 and run.summary["ok"] == 3
    pb = site(db, "pbcare-platform")
    assert pb.status == "ok" and pb.last_good_deployment_id == pb.current_deployment_id
    assert db.scalars(select(Incident)).all() == []


# ------------------------------------------------------------------------------------------------ the night
async def test_full_night(db, seeded, nc, world):
    await baseline(db, nc)
    good = site(db, "pbcare-platform").last_good_deployment_id
    world.break_things()
    run = await nc.night_shift(db).run_pass("night", "test")
    assert run.status == "done", run.error

    # PB CARe: down after a bad deploy -> instant rollback, verified, then a fix PR waiting for approval
    pb = site(db, "pbcare-platform")
    down = db.scalars(select(Incident).where(Incident.site_id == pb.id, Incident.kind == "site_down")).one()
    assert down.status == "mitigated" and "rollback" in down.resolution
    assert f"rollback:{pb.vercel_project_id}:{good}" in world.vercel.calls
    rb = db.scalars(select(NightAction).where(NightAction.incident_id == down.id, NightAction.action == "rollback")).one()
    assert rb.status == "verified"
    fix = db.scalars(select(FixProposal).where(FixProposal.site_id == pb.id)).one()
    assert fix.pr_number and fix.preview_status == "ok" and fix.status == "proposed"
    assert "src/app/contatti/page.tsx" in fix.diff and DEMO_BUG in fix.diff
    appr = db.get(Approval, fix.approval_id)
    assert appr.status == "pending" and (appr.tool_name, appr.action, appr.level) == ("github", "merge_pr", 4)

    # Prosperya: build failed on a network error -> one redeploy -> online
    pr = site(db, "prosperya-crm")
    build = db.scalars(select(Incident).where(Incident.site_id == pr.id, Incident.kind == "deploy_failed")).one()
    assert build.status == "resolved" and build.details["transient"] is True
    assert not db.scalars(select(FixProposal).where(FixProposal.site_id == pr.id)).first()

    # CF: vulnerable dependencies are reported, nothing touched
    cf = site(db, "cf-materassi-sito")
    vuln = db.scalars(select(Incident).where(Incident.site_id == cf.id)).one()
    assert vuln.kind == "vulnerabilities" and vuln.status == "open" and cf.status == "warning"

    # content draft prepared for the planned idea
    item = db.scalars(select(ContentItem).where(ContentItem.title == "Dietro le quinte del negozio")).one()
    assert item.status == "draft" and "Bozza" in item.body and item.generated_by_run_id == run.id

    # critical alert sent immediately, once
    assert len(nc.notifier.sent) == 1 and "pbcare-platform" in nc.notifier.sent[0]

    b = latest_or_live(db, seeded, nc.settings)
    assert "rollback" in b.text.lower() or "versione" in b.text
    assert f"PR #{fix.pr_number}" in b.text and "Da decidere" in b.text and "Bozza" not in b.text.split("\n")[0]
    assert "rilanciata" in b.text and "vulnerabilità" in b.text
    assert len(b.pending) == 1 and b.pending[0]["fix_id"] == fix.id

    # a second pass the same night does not repeat actions or proposals
    await nc.night_shift(db).run_pass("light", "test")
    assert db.scalars(select(FixProposal)).all() == [fix]
    assert len(db.scalars(select(NightAction).where(NightAction.action == "rollback")).all()) == 1


async def test_approve_fix_merges_promotes_and_verifies(db, seeded, nc, world):
    await baseline(db, nc)
    world.break_things()
    await nc.night_shift(db).run_pass("night", "test")
    fix = db.scalars(select(FixProposal)).one()
    out = await A.resolve(db, nc, fix.approval_id, approve=True)
    assert out["ok"] and out["executed"] and fix.status == "merged"
    db.commit()
    await drain_background()
    pb = site(db, "pbcare-platform")
    assert any(c.startswith(f"promote:{pb.vercel_project_id}") for c in world.vercel.calls)  # rollback pinned prod
    db.expire_all()
    verify = db.scalars(select(NightAction).where(NightAction.action == "verify")).one()
    assert verify.status == "verified"
    inc = db.get(Incident, fix.incident_id)
    assert inc.status == "resolved" and "verificata" in inc.resolution
    run = await nc.night_shift(db).run_pass("light", "test")
    assert site(db, "pbcare-platform").status == "ok", run.summary


async def test_reject_fix_closes_pr(db, seeded, nc, world):
    await baseline(db, nc)
    world.break_things()
    await nc.night_shift(db).run_pass("night", "test")
    fix = db.scalars(select(FixProposal)).one()
    out = await A.resolve(db, nc, fix.approval_id, approve=False)
    assert out["ok"] and fix.status == "rejected"
    assert world.github.prs[(f"{DEMO_OWNER}/pbcare-platform", fix.pr_number)]["state"] == "closed"
    assert fix.branch not in world.github.repos[f"{DEMO_OWNER}/pbcare-platform"]["branches"]
    with pytest.raises(A.ApprovalError):
        await A.resolve(db, nc, fix.approval_id, approve=True)  # already decided


async def test_rollback_waits_for_approval_when_auto_rollback_off(db, seeded, world):
    c = make_container(world, Settings(night_auto_rollback=False, night_fix_prs=False))
    await baseline(db, c)
    world.break_things()
    await c.night_shift(db).run_pass("night", "test")
    act = db.scalars(select(NightAction).where(NightAction.action == "rollback")).one()
    assert act.status == "pending_approval" and not any(x.startswith("rollback") for x in world.vercel.calls)
    out = await A.resolve(db, c, act.approval_id, approve=True)
    assert out["ok"] and any(x.startswith("rollback") for x in world.vercel.calls)
    assert db.get(Incident, act.incident_id).status == "mitigated"


async def test_no_rollback_when_problem_predates_deploy(db, seeded, nc, world):
    await baseline(db, nc)
    pb = world.projects["pbcare-platform"]
    world.vercel.health[pb.production_deployment_id] = SimHealth(status=503, body="down")  # same deploy breaks
    await nc.night_shift(db).run_pass("light", "test")
    act = db.scalars(select(NightAction).where(NightAction.action == "rollback")).one()
    assert act.status == "skipped" and "non viene dall'ultimo deploy" in act.summary
    assert not any(x.startswith("rollback") for x in world.vercel.calls)


async def test_site_policy_can_disable_fixes(db, seeded, nc, world):
    await baseline(db, nc)
    site(db, "pbcare-platform").policy = {"fix_prs": False}
    world.break_things()
    await nc.night_shift(db).run_pass("night", "test")
    assert db.scalars(select(FixProposal)).all() == []


async def test_without_coding_model_no_fix_but_diagnosis_note(db, seeded, world):
    c = make_container(world, with_coder=False)
    await baseline(db, c)
    world.break_things()
    await c.night_shift(db).run_pass("night", "test")
    assert db.scalars(select(FixProposal)).all() == []
    act = db.scalars(select(NightAction).where(NightAction.action == "fix_pr")).first()
    assert act.status == "skipped" and "modello" in act.summary
    drafts = db.scalars(select(NightAction).where(NightAction.action == "draft")).one()
    assert drafts.status == "skipped"


async def test_fix_limit(db, seeded, world):
    c = make_container(world, Settings(night_max_fixes=0))
    await baseline(db, c)
    world.break_things()
    await c.night_shift(db).run_pass("night", "test")
    act = db.scalars(select(NightAction).where(NightAction.action == "fix_pr")).first()
    assert act.status == "skipped" and "Limite" in act.summary


async def test_incident_resolves_by_itself(db, seeded, nc, world):
    await baseline(db, nc)
    pb = world.projects["pbcare-platform"]
    world.vercel.health[pb.production_deployment_id] = SimHealth(pages={"/contatti": 404})
    await nc.night_shift(db).run_pass("light", "test")
    inc = db.scalars(select(Incident).where(Incident.kind == "page_broken")).one()
    assert inc.status == "open" and inc.severity == "warning"
    world.vercel.health[pb.production_deployment_id] = SimHealth()
    await nc.night_shift(db).run_pass("light", "test")
    assert inc.status == "resolved"


# ------------------------------------------------------------------------------------------------ fixer rails
def test_fix_validation_rails():
    orig = {"src/a.ts": "const a = 1;\n" * 60}
    ok = FixAgent.validate('{"diagnosis":"d","confidence":"high","title":"fix: a","files":'
                           '[{"path":"src/a.ts","content":"' + ("const a = 1;\\n" * 59) + 'const a = 2;\\n"}]}', orig)
    assert ok.ok and ok.stats == [{"path": "src/a.ts", "added": 1, "removed": 1}]
    assert not FixAgent.validate('{"files":[{"path":"src/b.ts","content":"x"}]}', orig).ok        # not shown
    assert not FixAgent.validate('{"files":[{"path":"src/a.ts","content":"x"}]}', orig).ok        # truncated
    assert not FixAgent.validate("not json", orig).ok
    env = {".env": "A=1\n"}
    assert "protetto" in FixAgent.validate('{"files":[{"path":".env","content":"A=2\\n"}]}', env).reason
    big = {"src/a.ts": "x\n" * 400}
    assert "troppo ampia" in FixAgent.validate(
        '{"files":[{"path":"src/a.ts","content":"' + "y\\n" * 400 + '"}]}', big).reason
    diag = FixAgent.validate('{"diagnosis":"non so","files":[]}', orig)
    assert not diag.ok and diag.diagnosis == "non so"


# ------------------------------------------------------------------------------------------------ scheduler
def _at(h, m=0, day=10):
    tz = tzinfo("Europe/Rome")
    return datetime(2026, 10, day, h, m, tzinfo=tz).astimezone(UTC)


def test_scheduler_decisions():
    s, tz = Settings(), tzinfo("Europe/Rome")
    assert decide(_at(0, 30), SchedulerState(), s, tz) == []                       # before the window
    assert decide(_at(1, 5), SchedulerState(), s, tz) == ["night"]
    assert decide(_at(3, 0), SchedulerState(), s, tz) == ["night"]                 # catch-up: PC was off at 01:00
    st = SchedulerState(last_night_start=_at(1, 1), last_check_end=_at(1, 20))
    assert decide(_at(2, 0), st, s, tz) == []
    assert decide(_at(2, 21), st, s, tz) == ["light"]
    assert decide(_at(7, 40), st, s, tz) == ["briefing"]
    assert decide(_at(7, 40), SchedulerState(last_briefing=_at(7, 31)), s, tz) == []
    assert decide(_at(18, 0), SchedulerState(), s, tz) == []                        # no stale good-morning
    assert decide(_at(2, 0), SchedulerState(running=True), s, tz) == []
    day = Settings(day_interval_minutes=120)
    assert decide(_at(14, 0), SchedulerState(last_check_end=_at(11, 0), last_briefing=_at(7, 30)), day, tz) == ["light"]


def test_window_crossing_midnight():
    tz = tzinfo("Europe/Rome")
    start, end, inside = window_bounds(_at(0, 30), "23:00", "05:00", tz)
    assert inside and start == _at(23, 0, day=9) and end == _at(5, 0)
    s = Settings(night_start="23:00", night_end="05:00")
    assert decide(_at(23, 10), SchedulerState(last_night_start=_at(23, 0, day=8)), s, tz) == ["night"]


async def test_scheduler_tick_runs_and_briefs(db, seeded, nc, world, engine):
    from nodo.night.scheduler import NightScheduler
    await baseline(db, nc)
    db.commit()
    sched = NightScheduler(nc)
    jobs = await sched.tick(_at(1, 10))
    assert jobs == ["night"]
    assert db.scalars(select(NightRun).where(NightRun.kind == "night")).one().status == "done"
    out = await sched.briefing(now=datetime.now(UTC), trigger="test")
    assert out["sent"] and nc.notifier.sent[-1].startswith(("Buongiorno", "Buon pomeriggio", "Buonasera"))


# ------------------------------------------------------------------------------------------------ conversation
async def test_ask_nodo_about_the_night(db, seeded, nc, world):
    await baseline(db, nc)
    world.break_things()
    await nc.night_shift(db).run_pass("night", "test")
    core = nc.core(db)
    events = [e async for e in core.handle("Nodo, cosa hai fatto stanotte?")]
    done = events[-1]
    assert done["intent"] == "night_report" and "PR #" in done["text"] and "Dimmi sì o no" in done["text"]
    bound = [e for e in events if e["type"] == "approval.requested"]
    assert bound and bound[0]["approval_id"] == db.scalars(select(FixProposal)).one().approval_id

    st = await core.run_once("come stanno i siti?")
    assert st.intent == "sites_status" and "cf-materassi-sito" in st.text
    pend = await core.run_once("cosa devo approvare?")
    assert pend.intent == "pending_approvals" and "correzione" in pend.text
    chk = await core.run_once("controlla i siti adesso")
    assert chk.intent == "run_checks" and "Ho appena controllato 3 siti" in chk.text
    today = await core.run_once("Nodo, buongiorno. Cosa abbiamo oggi?")
    assert today.intent == "today" and "Turno di notte" in today.text


def test_briefing_when_nothing_ran(db, seeded):
    b = compose_briefing(db, seeded, datetime.now(UTC) - timedelta(hours=10))
    assert "non ho fatto controlli" in b.text and b.runs == 0
