"""The brain's wider toolbox: clients, projects, tasks, reminders, site code, deployments, web, drafts, memory."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nodo.app import build_container
from nodo.brain.extra import html_to_text, public_url
from nodo.brain.tools import Toolbox
from nodo.config import Settings
from nodo.db.models import Client, Conversation, MemoryFact, Message, Project, Reminder, Site, Task
from nodo.night.notify import FakeNotifier
from nodo.night.scheduler import NightScheduler
from nodo.providers.deterministic import FakeModelProvider


@pytest.fixture
async def box(db, seeded, world):
    c = build_container(Settings(), providers=[world.model, FakeModelProvider()], vercel=world.vercel,
                        repo=world.github, prober=world.prober, notifier=FakeNotifier())
    await c.night_shift(db).discover()
    core = c.core(db)
    conv = Conversation(organization_id=core.org, channel="text", active_context={})
    db.add(conv)
    db.flush()
    return Toolbox(core, conv, "r1", "test")


async def test_clients_and_projects_can_be_created_and_changed(box, db):
    out = await box.call("create_client", {"name": "Pizzeria Timilia", "sector": "ristorazione"})
    assert out["created"] and out["client"] == "Pizzeria Timilia"
    assert not (await box.call("create_client", {"name": "pizzeria timilia"}))["created"]   # no duplicates
    out = await box.call("create_project", {"name": "Sito Timilia", "client": "Timilia", "kind": "software",
                                            "priority": 2, "due_date": "2026-12-01"})
    assert out["created"] and out["client"] == "Pizzeria Timilia"
    assert not (await box.call("create_project", {"name": "Altro", "client": "Cliente Inesistente"}))["created"]
    out = await box.call("update_project", {"name": "Sito Timilia", "status": "paused", "priority": 1})
    assert out["updated"] and db.scalars(select(Project).where(Project.name == "Sito Timilia")).one().status == "paused"
    assert not (await box.call("update_project", {"name": "Sito Timilia", "status": "boh"}))["updated"]
    card = await box.call("clients", {"name": "Timilia"})
    assert card["client"] == "Pizzeria Timilia" and card["projects"][0]["name"] == "Sito Timilia"
    assert "Pizzeria Timilia" in [c["name"] for c in (await box.call("clients", {}))["clients"]]


async def test_tasks_can_be_listed_and_updated(box, db):
    await box.call("create_task", {"title": "Chiamare il commercialista", "priority": 2})
    await box.call("create_task", {"title": "Scrivere il post di Natale"})
    out = await box.call("update_task", {"task": "commercialista", "due_date": "2026-10-20", "status": "in corso"})
    assert out["updated"] and set(out["changes"]) >= {"scadenza", "stato → IN_PROGRESS"}
    out = await box.call("update_task", {"task": "post di natale", "new_title": "Scrivere il post di San Silvestro",
                                         "status": "fatta"})
    assert out["updated"]
    open_titles = [t["title"] for t in (await box.call("list_tasks", {}))["tasks"]]
    assert "Scrivere il post di San Silvestro" not in open_titles
    done = [t["title"] for t in (await box.call("list_tasks", {"include_done": True}))["tasks"]]
    assert "Scrivere il post di San Silvestro" in done
    assert not (await box.call("update_task", {"task": "qualcosa che non esiste"}))["updated"]
    assert not (await box.call("update_task", {"task": "commercialista", "status": "boh"}))["updated"]
    assert db.scalars(select(Task).where(Task.title.like("%commercialista%"))).one().due_date is not None


async def test_reminders_are_delivered_when_due(box, db):
    soon = (datetime.now(box._tz()) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    out = await box.call("set_reminder", {"text": "Richiamare Carlo", "when": soon})
    assert out["set"]
    assert not (await box.call("set_reminder", {"text": "ieri", "when": "2020-01-01T10:00"}))["set"]
    assert not (await box.call("set_reminder", {"text": "x", "when": "domani"}))["set"]
    assert [r["text"] for r in (await box.call("list_reminders", {}))["reminders"]] == ["Richiamare Carlo"]
    assert (await box.call("today_agenda", {}))["reminders"][0]["text"] == "Richiamare Carlo"
    db.commit()
    sched = NightScheduler(box.c)
    assert await sched.deliver_reminders(datetime.now(UTC)) == []                      # not due yet
    assert await sched.deliver_reminders(datetime.now(UTC) + timedelta(hours=3)) == ["Richiamare Carlo"]
    assert await sched.deliver_reminders(datetime.now(UTC) + timedelta(hours=4)) == []   # only once
    assert any("Richiamare Carlo" in m for m in box.c.notifier.sent)
    db.expire_all()
    assert db.scalars(select(Reminder)).one().status == "sent"
    assert any("Richiamare Carlo" in m.content for m in db.scalars(select(Message)))


async def test_reminder_can_be_cancelled(box):
    soon = (datetime.now(box._tz()) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    await box.call("set_reminder", {"text": "Pagare l'F24", "when": soon})
    assert (await box.call("cancel_reminder", {"text": "F24"}))["cancelled"]
    assert (await box.call("list_reminders", {}))["reminders"] == []


async def test_forget_removes_a_memory(box, db):
    await box.call("remember", {"fact": "Gaetano preferisce le riunioni al mattino"})
    assert (await box.call("recall", {"query": "riunioni mattino"}))["results"]
    out = await box.call("forget", {"query": "riunioni al mattino"})
    assert out["forgotten"]
    assert (await box.call("recall", {"query": "riunioni mattino"}))["results"] == []
    assert not (await box.call("forget", {"query": "argomento mai nominato xyz"}))["forgotten"]
    assert db.scalars(select(MemoryFact).where(MemoryFact.content.like('%riunioni al mattino%'))).one().valid_until is not None


async def test_site_code_and_deployments_are_readable(box, db, world):
    files = await box.call("list_site_files", {"site": "prosperya"})
    assert "src/app/page.tsx" in files["files"]
    page = await box.call("read_site_file", {"site": "prosperya", "path": "src/app/page.tsx"})
    assert "content" in page and page["content"]
    assert "segreti" in (await box.call("read_site_file", {"site": "prosperya", "path": ".env.local"}))["error"]
    assert "non esiste" in (await box.call("read_site_file", {"site": "prosperya", "path": "nope.txt"}))["error"]
    assert (await box.call("site_commits", {"site": "prosperya"}))["commits"]
    deps = await box.call("site_deployments", {"site": "prosperya"})
    assert deps["deployments"] and deps["deployments"][0]["state"] == "READY"
    assert "nessun sito" in (await box.call("site_commits", {"site": "zzzzzz"}))["error"]


async def test_check_url_and_fetch_refuse_private_addresses(box):
    assert public_url("http://127.0.0.1:8000/x") is None
    assert public_url("http://localhost/x") is None
    assert public_url("file:///etc/passwd") is None
    assert public_url("http://192.168.1.1") is None
    assert "non pubblico" in (await box.call("fetch_url", {"url": "http://127.0.0.1:8000/api/v1/setup"}))["error"]
    assert "non pubblico" in (await box.call("check_url", {"url": "http://10.0.0.5"}))["error"]


def test_html_to_text_keeps_the_words_only():
    raw = ("<html><head><style>p{color:red}</style><script>alert(1)</script></head><body><h1>Titolo</h1>"
           "<p>Ciao &amp; benvenuti</p><!-- nascosto --></body></html>")
    text = html_to_text(raw)
    assert "Titolo" in text and "Ciao & benvenuti" in text and "alert" not in text and "color" not in text


async def test_drafts_can_be_listed_and_updated(box, db):
    await box.call("create_client", {"name": "Bar Sole"})
    assert (await box.call("save_draft", {"title": "Post estate", "body": "Bevi fresco", "client": "Bar Sole"}))["saved"]
    drafts = await box.call("list_drafts", {"client": "Bar Sole"})
    assert drafts["contents"][0]["title"] == "Post estate" and drafts["contents"][0]["status"] == "draft"
    out = await box.call("update_draft", {"title": "post estate", "status": "approved", "body": "Bevi fresco, sempre"})
    assert out["updated"]
    assert (await box.call("list_drafts", {"status": "approved"}))["contents"][0]["preview"].endswith("sempre")
    assert not (await box.call("update_draft", {"title": "post estate", "status": "boh"}))["updated"]
    assert db.scalars(select(Client).where(Client.name == "Bar Sole")).one()


async def test_notify_me_and_health(box):
    assert (await box.call("notify_me", {"text": "ciao"}))["sent"]
    assert box.c.notifier.sent[-1] == "ciao"
    assert (await box.call("nodo_health", {}))["checks"]


async def test_every_tool_has_a_spec_the_models_can_use(box):
    names = {s.name for s in box.specs()}
    assert {"clients", "create_project", "update_task", "set_reminder", "forget", "read_site_file", "fetch_url",
            "site_deployments", "update_draft", "nodo_health"} <= names
    for s in box.specs():
        assert s.parameters["type"] == "object" and s.description
    assert db_sites(box) >= 1


def db_sites(box) -> int:
    return len(box.s.scalars(select(Site)).all())


WORK_CLAUDE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True}}))
    sys.exit(0)
prompt = sys.stdin.read()
open(os.environ["FAKE_WORK_LOG"], "w").write(json.dumps({{"args": args, "cwd": os.getcwd(), "prompt": prompt}}))
open("piano.md", "w").write("# Piano\\n1. Partire\\n")
open(".nascosto", "w").write("no")
print(json.dumps({{"type": "result", "is_error": False, "result": "Ho scritto il piano."}}))
'''


@pytest.mark.skipif(sys.platform == "win32", reason="the fake CLI is a shebang script")
async def test_long_work_runs_in_its_own_folder_and_reports_back(box, db, tmp_path, monkeypatch):
    import json
    import stat

    from nodo.brain import workbench as W
    from nodo.providers.claude_cli import ClaudeCodeProvider
    from nodo.services.files import FileStore

    exe = tmp_path / "claude"
    exe.write_text(WORK_CLAUDE.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_WORK_LOG", str(tmp_path / "log.json"))
    box.c.providers.insert(0, ClaudeCodeProvider("sonnet", executable=str(exe)))
    box.c.files = FileStore(tmp_path / "files")
    out = await box.call("start_work", {"task": "Prepara un piano di lancio per il sito di Bar Sole"})
    assert out["started"]
    db.commit()
    await W.drain()
    db.expire_all()
    seen = json.loads((tmp_path / "log.json").read_text())
    assert "piano di lancio" in seen["prompt"] and "lavori" in seen["cwd"]          # its own folder
    tools = seen["args"][seen["args"].index("--tools") + 1]
    assert "Bash" not in tools and "Write" in tools                                  # no shell
    msg = [m.content for m in db.scalars(select(Message).where(Message.role == "nodo"))]
    assert any("Ho scritto il piano" in m and "piano.md" in m for m in msg)
    assert not any(".nascosto" in m for m in msg)
    assert any(f.name.endswith("piano.md") for f in (tmp_path / "files").iterdir())
    assert any("Ho scritto il piano" in m for m in box.c.notifier.sent)


async def test_long_work_runs_even_without_the_subscription(box):
    # 18/10: the Claude program had hit its weekly limit; now another model does the work, visibly
    from nodo.brain.workbench import drain
    out = await box.call("start_work", {"task": "fai qualcosa"})
    assert out["started"] is True
    await drain()
