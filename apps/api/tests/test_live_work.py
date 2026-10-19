"""18/10: the site that was promised for two hours, the live site touched by mistake, the popups that closed
themselves. Long jobs you can watch, the truth about them, and the guards."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nodo.app import build_container
from nodo.brain import live_work
from nodo.config import Settings
from nodo.providers.deterministic import FakeModelProvider
from tests.test_brain_extra import box  # noqa: F401  (fixture)
from tests.test_knowledge import ScriptedModel

PAGE = ("<!DOCTYPE html><html lang='it'><head><style>body{font-family:sans-serif}</style></head><body>"
        + "<section><h1>HostFacility</h1><p>Servizi per strutture ricettive.</p></section>" * 40 + "</body></html>")


@pytest.fixture(autouse=True)
def _clean_jobs():
    live_work._jobs.clear()
    yield
    live_work._jobs.clear()


def test_the_lavagna_says_when_nothing_is_running(tmp_path):
    from nodo.night.briefing import tzinfo
    tz = tzinfo("Europe/Rome")
    assert "nessuno in corso" in live_work.board_line(tz)
    live_work.start("j1", "Sito HostFacility", tmp_path, kind="sito")
    (tmp_path / "index.html").write_text("<html>\n<body>\nciao")
    assert "IN CORSO" in live_work.board_line(tz)
    snap = live_work.snapshot()[0]
    assert snap["status"] == "in corso" and snap["file"] == "index.html" and "ciao" in snap["tail"]
    live_work.finish("j1", False, "limite settimanale")
    assert "NON RIUSCITO" in live_work.board_line(tz) and not live_work.running()


async def test_a_site_is_written_into_a_file_you_can_watch_and_opens_when_ready(db, seeded):
    db.commit()
    model = ScriptedModel("```html\n" + PAGE + "\n```")
    c = build_container(Settings(), providers=[model, FakeModelProvider()])
    from nodo.brain.builder import _tasks, start_site
    job = start_site(c, seeded, "HostFacility", "facility management per hotel", None)
    assert live_work.running() and live_work.running()[0].kind == "sito"
    await asyncio.gather(*list(_tasks))
    snap = live_work.snapshot()[0]
    assert snap["status"] == "finito" and snap["view"] == "web" and snap["url"].endswith(".html"), snap
    written = (Path(c.files.root) / "lavori" / job / "index.html").read_text()
    assert written.startswith("<!DOCTYPE html>") and "```" not in written


async def test_a_job_without_the_claude_program_is_done_by_another_model(db, seeded):
    class LimitedAgent:
        name = "claude-subscription"

        def models(self):
            return []

        async def run_agent(self, task, workdir, system, timeout=900):
            raise RuntimeError("Claude (abbonamento) errore: You've hit your weekly limit")
    db.commit()
    model = ScriptedModel(PAGE)
    c = build_container(Settings(), providers=[model, FakeModelProvider()])
    c.providers.insert(0, LimitedAgent())
    from nodo.brain.workbench import drain, start_work
    start_work(c, seeded, "Crea la bozza del sito HostFacility in HTML", None)
    await drain()
    snap = live_work.snapshot()[0]
    assert snap["status"] == "finito" and snap["url"], snap


async def test_a_live_site_is_never_changed_unless_he_named_it(box):  # noqa: F811
    box.user_text = "fammi la bozza del sito HostFacility"
    out = await box.call("request_site_change", {"site": "pizzeria", "instruction": "metti la landing HostFacility"})
    assert out.get("started") is False and ("non ha nominato" in out.get("error", "")
                                            or "nessun sito" in out.get("error", "")), out


async def test_the_screen_closes_only_when_he_asks(box):  # noqa: F811
    box.user_text = "fammi vedere il sospeso di Carmelo"
    assert (await box.call("close_screen", {})).get("error")
    box.user_text = "ok chiudi pure"
    assert (await box.call("close_screen", {}))["closed"]


def test_the_same_document_made_twice_is_not_a_doubt():
    from nodo.brain.extra import _same_file_newest
    rows = [{"title": "Sospeso Carmelo", "uri": "/api/v1/files/sospeso_carmelo-2.pdf"},
            {"title": "Sospeso Carmelo", "uri": "/api/v1/files/sospeso_carmelo.pdf"}]
    assert _same_file_newest(rows, rows) is rows[0]
    other = [{"title": "A", "uri": "/f/a.pdf"}, {"title": "B", "uri": "/f/b.pdf"}]
    assert _same_file_newest(other, other) is None


def test_other_alphabets_in_front_of_italian_go_away():
    from nodo.memory.mind import clean_heard, is_babble
    assert clean_heard("या non devi ascoltare il video") == "non devi ascoltare il video"
    assert not is_babble("Ci sei?") and not is_babble("Mi senti?")


def test_a_website_request_is_done_not_promised():
    from nodo.brain.quick_read import match_action
    tool, args = match_action("Ti ho detto fammi una bozza del sito di come sarà.",
                              "user: HostFacility è il nome che abbiamo pensato per il sito")
    assert tool == "create_website" and args["name"] == "HostFacility"
    assert match_action("modifica il sito di arredo chef") is None


def test_voice_promises_are_recognised():
    from nodo.voice.runtime import _PROMISE
    for said in ("Ci sto lavorando, ti avviso appena è pronta.", "Siamo circa all'ottanta percento",
                 "Sto finendo gli ultimi dettagli", "Siamo al 80%"):
        assert _PROMISE.search(said), said
    assert not _PROMISE.search("Ecco il riepilogo di ieri.")
