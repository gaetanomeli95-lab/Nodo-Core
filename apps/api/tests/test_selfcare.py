"""NODO improves by itself at night: rereads the day, writes lessons, studies, fixes its own code (with a yes).
Also: the old-chats reading that survives a restart and tells the truth, and the 15/10 conversation's errors."""
from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from nodo.app import build_container
from nodo.config import Settings
from nodo.db.models import Approval, Conversation, KnowledgeSource, LedgerEntry, Message, NightAction, NightRun
from nodo.db.session import session_scope
from nodo.ledger import Ledger
from nodo.night import selfcare
from nodo.providers.deterministic import FakeModelProvider
from tests.test_brain_extra import box  # noqa: F401  (fixture)
from tests.test_knowledge import ScriptedModel

REVIEW = ('{"riassunto": "Giornata buona, ma ho recitato invece di cantare.", '
          '"lezioni": ["Quando Gaetano chiede una canzone, fai partire la base e canta a tempo, non recitare.", '
          '"Quando Gaetano chiede cosa sai della sua salute, diglielo: è suo."], '
          '"da_studiare": ["come si canta una ninna nanna siciliana"], '
          '"codice": [{"problema": "la base musicale non parte", "prove": "Tu non canti. Parli.", '
          '"idea": "far partire la base dal server"}]}')


def _day(db, at: datetime, org: str) -> None:
    conv = Conversation(organization_id=org, channel="voice", active_context={})
    db.add(conv)
    db.flush()
    lines = [("user", "Maria, quanto ho incassato oggi?"), ("nodo", "Oggi 350 euro."),
             ("user", "Cantami una canzone, sono triste."), ("nodo", "Non essere triste, Gaetano mio…"),
             ("user", "Sì, ma questa non è una canzone, è una poesia. Tu non canti. Parli."),
             ("nodo", "Hai ragione, posso fare di meglio!")]
    for i, (role, text) in enumerate(lines):
        db.add(Message(conversation_id=conv.id, role=role, content=text, created_at=at + timedelta(seconds=i)))
    db.flush()


def test_the_dossier_keeps_the_complaints_with_their_context(db, seeded):
    at = datetime.now(UTC) - timedelta(hours=3)
    _day(db, at, seeded)
    text = selfcare.dossier(db, seeded, at - timedelta(minutes=1), datetime.now(UTC))
    assert "Tu non canti. Parli." in text and "Cantami una canzone" in text and "Hai ragione" in text
    assert "Oggi 350 euro" in text                # the lines before give the context


def test_a_review_is_parsed_even_with_words_around_it():
    r = selfcare.parse_review("Ecco:\n" + REVIEW + "\nFine.")
    assert len(r.lessons) == 2 and r.study and r.code[0]["problema"] == "la base musicale non parte"
    assert selfcare.parse_review("non so").lessons == []


async def test_the_night_writes_lessons_once_and_maria_reads_them(db, seeded):
    at = datetime.now(UTC) - timedelta(hours=3)
    _day(db, at, seeded)
    db.commit()
    model = ScriptedModel(REVIEW)
    c = build_container(Settings(), providers=[model, FakeModelProvider()])
    out = await selfcare.run(c, seeded)
    assert len(out["lessons"]) == 2 and model.calls == 1
    with session_scope() as s:
        facts = selfcare.lessons(s, seeded)
        assert [f.layer for f in facts] == ["lesson", "lesson"] and facts[0].source_type == "agent"
        run = s.scalars(select(NightRun).where(NightRun.kind == "selfcare")).one()
        assert "recitato" in run.summary["text"] and "lezioni nuove" in run.summary["text"]
        assert s.scalars(select(NightAction).where(NightAction.action == "selfcare")).one()
        from nodo.memory.mind import memory_context
        from nodo.night.briefing import tzinfo
        ctx = memory_context(s, seeded, tzinfo("Europe/Rome"))
        assert "LEZIONI CHE HAI IMPARATO" in ctx and "canta a tempo" in ctx
        line = selfcare.last_night(s, seeded, tzinfo("Europe/Rome"))
        assert line and "recitato" in line
    # the next night, the same lessons are not written twice (and the day already read is not reread)
    _day(db, datetime.now(UTC) - timedelta(minutes=30), seeded)
    db.commit()
    out = await selfcare.run(c, seeded)
    assert out["lessons"] == []
    with session_scope() as s:
        assert len(selfcare.lessons(s, seeded)) == 2


def test_selfcare_is_due_once_per_night(db, seeded):
    now = datetime.now(UTC)
    start = now - timedelta(hours=1)
    assert selfcare.due(db, seeded, now, start)
    db.add(NightRun(organization_id=seeded, request_id="x", kind="selfcare", status="done", trigger="schedule",
                    summary={}, started_at=now - timedelta(minutes=10)))
    db.flush()
    assert not selfcare.due(db, seeded, now, start)
    assert selfcare.due(db, seeded, now + timedelta(days=1), start + timedelta(days=1))


# ------------------------------------------------------------------------------------------------ its own code
def _repo(tmp: Path) -> Path:
    repo = tmp / "nodo"
    (repo / "apps/api/nodo").mkdir(parents=True)
    (repo / "apps/api/tests").mkdir(parents=True)
    (repo / "apps/api/nodo/__init__.py").write_text("")
    (repo / "apps/api/tests/test_ok.py").write_text("def test_ok():\n    assert True\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo


class FakeCoder:
    def __init__(self, files: dict[str, str]):
        self.files, self.tasks = files, []

    async def run_agent(self, task, workdir, system, timeout=900):
        self.tasks.append((task, system))
        for rel, text in self.files.items():
            p = Path(workdir) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        return "Ho fatto partire la base dal server e aggiunto un test."


@pytest.mark.skipif(not shutil.which("git"), reason="git missing")
async def test_a_code_fix_is_tested_proposed_and_merged_only_with_a_yes(db, seeded, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(selfcare, "REPO", repo)
    monkeypatch.setattr(selfcare.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    c = build_container(Settings(), providers=[FakeModelProvider()])
    coder = FakeCoder({"apps/api/nodo/music.py": "STARTS_ON_SERVER = True\n",
                       "apps/api/tests/test_music.py": "from nodo.music import STARTS_ON_SERVER\n\n\n"
                                                       "def test_band():\n    assert STARTS_ON_SERVER\n"})
    db.commit()
    out = await selfcare.improve_code(c, seeded, coder, {"problema": "la base musicale non parte"}, datetime.now(UTC))
    assert out["status"] == "proposed", out
    assert "apps/api/nodo" in coder.tasks[0][1] and "Mai .env" in coder.tasks[0][1]
    assert not (repo / "apps/api/nodo/music.py").exists()            # nothing changes before the yes
    assert not list((tmp_path / "tmp" / "nodo-migliora").glob("*"))  # the copy is gone, the branch stays
    with session_scope() as s:
        a = s.scalars(select(Approval).where(Approval.tool_name == "nodo")).one()
        assert a.payload["files"] and a.status == "pending"
        from nodo.night.briefing import pending_decisions
        assert "miglioramento al mio codice" in pending_decisions(s, seeded)[0]["label"]
        from nodo.night.approvals import resolve
        res = await resolve(s, c, a.id, approve=True)
        assert res["ok"] and "Chiudimi e riaprimi" in res["message"], res
    assert (repo / "apps/api/nodo/music.py").read_text() == "STARTS_ON_SERVER = True\n"
    # a second problem while one waits? one at a time. A fix that touches the panel or the keys is refused.
    bad = FakeCoder({"apps/web/src/App.tsx": "x", "apps/api/nodo/x.py": "y = 1\n"})
    out = await selfcare.improve_code(c, seeded, bad, {"problema": "colori"}, datetime.now(UTC) + timedelta(minutes=1))
    assert out["status"] == "refused" and not (repo / "apps/web").exists()
    broken = FakeCoder({"apps/api/tests/test_bad.py": "def test_bad():\n    assert False\n"})
    out = await selfcare.improve_code(c, seeded, broken, {"problema": "x"}, datetime.now(UTC) + timedelta(minutes=2))
    assert out["status"] == "tests_failed"
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "migliora" not in branches                                # discarded attempts leave nothing behind


def test_allowed_paths():
    assert selfcare.allowed_paths(["apps/api/nodo/voice/runtime.py", "apps/api/tests/test_x.py"])
    assert not selfcare.allowed_paths(["apps/api/.env"]) and not selfcare.allowed_paths([])
    assert not selfcare.allowed_paths(["apps/web/src/main.tsx"])


# ------------------------------------------------------------------------------------------------ studying, honestly
def test_old_chats_progress_is_real_and_the_lavagna_says_it(db, seeded):
    from nodo.brain.board import build
    from nodo.db.models import Document, KnowledgeChunk
    from nodo.knowledge import distill
    src = KnowledgeSource(organization_id=seeded, kind="chatgpt", label="export.zip", status="distilling",
                          stats={"conversations": 3, "chunks": 3, "distilled": 1, "facts": 4})
    db.add(src)
    db.flush()
    for i, mark in enumerate((" ✓", "", "")):
        doc = Document(organization_id=seeded, title=f"chat {i}", kind="chat", summary="[chatgpt] 2025" + mark)
        db.add(doc)
        db.flush()
        db.add(KnowledgeChunk(source_id=src.id, document_id=doc.id, organization_id=seeded, seq=0, text="ciao",
                              happened_at=datetime.now(UTC)))
    db.flush()
    distill._progress_cache.clear()
    p = distill.progress(db, seeded)
    assert p["read"] == 1 and p["total"] == 3 and p["left"] == 2 and not p["running"]
    page = build(db, seeded, Settings(ledger_business="CF Materassi"), datetime.now(UTC))
    assert "STUDIO DELLE VECCHIE CHAT" in page and "lette 1 conversazioni su 3" in page and "stato: fermo" in page


async def test_a_reading_left_half_done_starts_again(db, seeded, monkeypatch):
    from nodo.knowledge import distill
    src = KnowledgeSource(organization_id=seeded, kind="chatgpt", label="x", status="distilling", stats={})
    db.add(src)
    db.commit()
    monkeypatch.setattr(distill, "_pending_documents", lambda db, sid, n: ["d1"])
    started = []
    monkeypatch.setattr(distill, "start", lambda c, sid: started.append(sid))
    assert distill.resume_pending(object()) == [src.id] and started == [src.id]


async def test_study_status_tool_tells_the_numbers(box):  # noqa: F811
    out = await box.call("study_status", {})
    assert "vecchie_chat" in out and "stanotte" in out and out["say"]


# ------------------------------------------------------------------------------------------------ 15/10 conversation
def test_the_same_expenses_said_twice_are_written_once(db, seeded):
    led = Ledger(db, seeded, "CF Materassi", today=date(2026, 10, 15))
    for item, who, amount in (("giornata", "Gaetano", 40), ("bar", "bar", 10)):
        led.add(kind="spesa", item=item, counterpart=who, amount=amount, day="13/10")
    db.flush()
    twin, exact = led.twin(kind="spesa", item="Gaetano", amount=40, day="13/10")
    assert twin is not None and exact
    twin, exact = led.twin(kind="spesa", item="bar", amount=10, day="13/10")
    assert exact
    assert led.twin(kind="spesa", item="palestra", amount=65, day="13/10") == (None, False)


async def test_ledger_add_does_not_double_rachid_and_asks_for_a_correction(box, db):  # noqa: F811
    first = {"kind": "vendita", "item": "materasso King Box ortopedico", "size": "160x190", "qty": 2,
             "unit_price": 140, "counterpart": "Rachid Marrocchino Zen"}
    out = await box.call("ledger_add", {"entries": [first]})
    assert out["saved"] == 1
    again = await box.call("ledger_add", {"entries": [first]})
    assert again["saved"] == 0 and again["already_there"]
    fix = await box.call("ledger_add", {"entries": [{**first, "payment": "sospeso", "amount": 140}]})
    assert fix["saved"] == 0 and fix["maybe_corrections"] and "ledger_update" in fix["say_fixes"]
    other = await box.call("ledger_add", {"entries": [{**first, "counterpart": "Rossi"}]})
    assert other["saved"] == 1                                    # same mattress to another customer: a new sale
    forced = await box.call("ledger_add", {"entries": [{**first, "again": True}]})
    assert forced["saved"] == 1
    rows = db.scalars(select(LedgerEntry).where(LedgerEntry.counterpart == "Rachid Marrocchino Zen")).all()
    assert len(rows) == 2


async def test_the_register_pdf_has_days_entrate_and_uscite(box, db):  # noqa: F811
    await box.call("ledger_add", {"entries": [
        {"kind": "vendita", "item": "materasso Arianna", "size": "160x190", "amount": 110, "counterpart": "Rossi",
         "day": "ieri"},
        {"kind": "spesa", "item": "bar", "amount": 10, "day": "ieri"},
        {"kind": "vendita", "item": "rete", "amount": 60, "counterpart": "Bianchi", "payment": "sospeso"}]})
    out = await box.call("ledger_pdf", {"from_day": "ieri", "to_day": "oggi"})
    assert out["format"] == "pdf" and out["url"].endswith(".pdf"), out
    from nodo.brain.surfaces import surface_for
    assert surface_for("ledger_pdf", out)["rows"][0]["kind"] == "pdf"
    from nodo.services.documents import extract_text
    path = box.c.files.get(out["url"].rsplit("/", 1)[-1])
    text = extract_text(path)
    for must in ("ENTRATE", "USCITE", "Rossi", "bar", "TOTALE DEL PERIODO", "Chi deve ancora pagare", "Bianchi",
                 "pagato", "sospeso"):
        assert must in text, must
    assert (await box.call("ledger_pdf", {"from_day": "boh"}))["error"]


async def test_the_last_minutes_of_conversation_in_a_pdf(box, db):  # noqa: F811
    _day(db, datetime.now(UTC) - timedelta(minutes=5), box.org)
    out = await box.call("show_transcript", {"minutes": 20})
    assert out["format"] == "pdf" and out["lines"] == 6
    assert (await box.call("show_transcript", {"minutes": "1"})).get("error")


def test_asking_for_a_song_starts_the_band():
    from nodo.voice.runtime import sing_style
    assert sing_style("É no, cantami una canzone, sono triste.") == "ballata"
    assert sing_style("Dobbiamo inventare pure una canzoncina per Marcello") == "pop"
    assert sing_style("cantale una ninna nanna") == "ninna nanna"
    assert sing_style("Sì, ma questa non è una canzone, è una poesia. Tu non canti. Parli.") is None
    assert sing_style("quanto ho incassato oggi") is None


def test_spanish_noise_is_not_a_question():
    from nodo.memory.mind import is_babble
    assert is_babble("saluda a la bicha.") and is_babble("Não sei o que fazer")
    assert not is_babble("Maria, quanto ho incassato oggi?") and not is_babble("cosa")


def test_maria_tells_gaetano_his_own_things():
    from nodo.brain.persona import LIVE_BRAIN, PERSONA
    assert "Non dire mai «non posso divulgarlo» al diretto interessato" in LIVE_BRAIN
    assert "a lui lo dici sempre" in PERSONA
    assert "STUDIO e STANOTTE" in LIVE_BRAIN


def test_noise_needs_to_last_before_it_cuts_maria():
    from nodo.voice.live import live_config
    cfg = live_config(Settings(), "x", [])
    assert cfg["realtime_input_config"]["automatic_activity_detection"]["prefix_padding_ms"] == 300
    quiet = live_config(Settings(live_interrupt=False), "x", [])
    assert quiet["realtime_input_config"]["activity_handling"] == "NO_INTERRUPTION"



def test_clear_orders_are_done_by_the_program_not_promised():
    from nodo.brain.quick_read import match_action
    assert match_action("fammi il pdf del registro dal 12 a oggi") == ("ledger_pdf", {"from_day": "12", "to_day": "oggi"})
    assert match_action("rifammi il pdf con la grafica perfetta", "user: fammi il pdf del registro")[0] == "ledger_pdf"
    assert match_action("crea un pdf con una poesia") is None
    assert match_action("mostrami gli ultimi 15 minuti di conversazione") == ("show_transcript", {"minutes": 15})


def test_maria_cannot_say_it_is_on_screen_when_it_is_not():
    from nodo.voice.runtime import _CLAIM, action_intent
    assert _CLAIM.search("Eccomi, te l'ho appena aperto sullo schermo.")
    assert _CLAIM.search("Il documento sta per comparire, arriverà subito.")
    assert not _CLAIM.search("Ci vuole un attimo, intanto lo preparo.")
    assert action_intent("rifammi il PDF")


def test_spanish_whats_are_noise_now():
    from nodo.memory.mind import is_babble
    assert is_babble("¿Qué?") and is_babble("¿Qué tal?") and is_babble("¿Llamas a las 9:30?")
    assert not is_babble("Che?") and not is_babble("cosa?")


def test_the_doubled_rows_of_15_10_are_corrected_once(db, seeded):
    from nodo import data_fixes
    from nodo.ledger import Ledger
    led = Ledger(db, seeded, "CF Materassi", today=date(2026, 10, 15))
    made = {}
    for key, row in {
        "684076c8": dict(kind="spesa", item="Gaetano", amount=40, day="13/10"),
        "03e816c0": dict(kind="spesa", item="Giorgia", amount=50, day="13/10"),
        "3a4009ff": dict(kind="spesa", item="bar", amount=10, day="13/10"),
        "65fd0bc2": dict(kind="spesa", item="lavaggio", amount=10, day="13/10"),
        "fc7484da": dict(kind="vendita", item="King Box", qty=2, amount=280, counterpart="Rachid Marrocchino Zen",
                         day="15/10"),
        "63f7f4d6": dict(kind="vendita", item="King Box H25", amount=70, counterpart="Rachid Marrocchino Zen",
                         day="15/10"),
        "fe365df3": dict(kind="vendita", item="King Box", qty=2, amount=140, counterpart="Rachid Marrocchino Zen",
                         payment="sospeso", day="15/10"),
        "09a2374c": dict(kind="vendita", item="King Box H25", amount=70, counterpart="Rachid Marrocchino Zen",
                         payment="sospeso", day="15/10")}.items():
        e = led.add(**row)
        e.id = key + e.id[8:]
        made[key] = e
    db.flush()
    done = data_fixes.apply(db, "CF Materassi")
    assert len(done) == 2
    assert all(made[k].status == "annullato" for k in ("684076c8", "03e816c0", "3a4009ff", "65fd0bc2", "fe365df3",
                                                       "09a2374c"))
    assert led.customer("Rachid")["due"] == 100.0
    assert led.report(date(2026, 10, 13), date(2026, 10, 13))["totals"]["uscite"] == 0.0
    assert data_fixes.apply(db, "CF Materassi") == []             # once only


def test_save_it_on_the_desktop_and_look_before_saying_it_is_not_there():
    from nodo.brain.quick_read import match_action, recall_query
    from nodo.voice.runtime import _CLAIM_SAVED
    assert match_action("Lo puoi salvare nel desktop.") == ("save_to_desktop", {"name": "l'ultimo"})
    assert match_action("fammi il pdf del registro e salvalo sul desktop")[1]["desktop"] is True
    assert match_action("mettimi la foto sul desktop") is None
    assert recall_query("Riesci a trovare il progetto che si chiama Regia? Quello per Tommaso.") == "Regia Tommaso"
    assert recall_query("quanto ho incassato oggi") is None
    assert _CLAIM_SAVED.search("Te l'ho messo sul desktop.") and _CLAIM_SAVED.search("Esatto, è già salvato sul desktop.")


async def test_the_same_file_made_twice_is_not_a_doubt(box, tmp_path, monkeypatch):  # noqa: F811
    first = await box.call("ledger_pdf", {})
    second = await box.call("ledger_pdf", {})
    assert first["url"] and second["url"]
    monkeypatch.setattr(type(box), "_desktop", staticmethod(lambda: tmp_path))
    out = await box.call("save_to_desktop", {"name": "registro-15-10.pdf"})
    assert out.get("saved"), out


def test_memory_work_leaves_gemini_flash_to_the_conversation():
    from nodo.db.models import Sensitivity
    from nodo.providers.base import ModelDescriptor
    from nodo.providers.router import ModelRouter, RouteRequest

    def desc(provider, model, reasoning):
        return ModelDescriptor(provider=provider, model=model, reasoning=reasoning, coding=3, tool_calling=True,
                               latency_class="low", tier="free", max_sensitivity=Sensitivity.INTERNAL)

    class P:
        def __init__(self, d):
            self.name, self.d = d.provider, d

        def models(self):
            return [self.d]
    router = ModelRouter([P(desc("gemini", "flash", 4)), P(desc("gemini-lite", "lite", 3))], mode="FREE")
    assert router.select(RouteRequest(purpose="brain")).descriptor.provider == "gemini"
    assert router.select(RouteRequest(purpose="memory")).descriptor.provider == "gemini-lite"


def test_nvidia_free_brain_joins_the_line_when_its_key_is_there():
    from nodo.providers.registry import build_providers
    names = [p.name for p in build_providers(Settings(nvidia_api_key="nvapi-test"))]
    assert "nvidia" in names
    assert "nvidia" not in [p.name for p in build_providers(Settings())]


async def test_a_brain_check_says_what_is_wrong_with_a_key():
    from nodo.providers.check import test, test_all

    class Bad:
        name = "nvidia"

        def models(self):
            from nodo.db.models import Sensitivity
            from nodo.providers.base import ModelDescriptor
            return [ModelDescriptor(provider="nvidia", model="m", reasoning=3, coding=3, tool_calling=True,
                                    max_sensitivity=Sensitivity.INTERNAL)]

        async def stream(self, model, messages, **kw):
            raise RuntimeError("nvidia HTTP 401: unauthorized")
            yield  # pragma: no cover

    class Good(Bad):
        name = "groq"

        async def stream(self, model, messages, **kw):
            from nodo.providers.base import ChatChunk
            yield ChatChunk(delta="ok")
    ok, msg = await test(Bad())
    assert not ok and msg.startswith("chiave non valida")
    assert await test(Good()) == (True, "funziona")
    res = await test_all([Bad(), Good()])
    assert res["groq"] == "funziona" and res["nvidia"].startswith("chiave non valida")


async def test_a_retired_model_is_replaced_by_itself():
    import httpx

    from nodo.providers.openai_compat import OpenAICompatProvider, free_cloud_descriptors, pick_model
    assert pick_model("groq", ["whisper-large-v3", "llama-3.1-8b-instant", "openai/gpt-oss-120b"]) == \
        "openai/gpt-oss-120b"
    assert pick_model("nvidia", ["nvidia/nv-embed", "meta/llama-4-maverick-17b-128e-instruct"]) == \
        "meta/llama-4-maverick-17b-128e-instruct"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "meta/llama-4-maverick-17b-128e-instruct"}]})
        import json as _j
        body = _j.loads(request.content)
        if body["model"] == "meta/llama-3.3-70b-instruct":
            return httpx.Response(410, json={"detail": "The model has reached its end of life"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})
    p = OpenAICompatProvider("nvidia", "https://x/v1", "k", free_cloud_descriptors("nvidia", "meta/llama-3.3-70b-instruct"),
                             transport=httpx.MockTransport(handler))
    turn = await p.complete_tools("meta/llama-3.3-70b-instruct", [{"role": "user", "content": "ciao"}], [])
    assert turn.text == "ok" and p.models()[0].model == "meta/llama-4-maverick-17b-128e-instruct"


def test_an_item_of_a_lump_debt_is_asked_not_added_on_top(db, seeded):
    from nodo.ledger import Ledger, check_entry
    led = Ledger(db, seeded, "CF Materassi", today=date(2026, 10, 15))
    led.add(kind="credito", item="sospeso Enrico Patti Cash (conto vendita)", amount=1410,
            counterpart="Enrico Patti Cash", note="Elenco dei prodotti da dettagliare", day="14/10")
    e = led.add(kind="credito", item="poltroncina in pelle", amount=150, counterpart="Enrico Patti Cash",
                payment="sospeso")
    db.flush()
    assert any("FA PARTE" in w for w in check_entry(led, e))
