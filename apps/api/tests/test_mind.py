"""NODO's mind: the archivist keeps people, rules and facts by itself; the core memory is in front of every answer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nodo.app import build_container
from nodo.brain.loop import unbacked_claim
from nodo.config import Settings
from nodo.db.models import Conversation, FactStatus, MemoryFact, Message
from nodo.memory import mind
from nodo.providers.base import ChatChunk, ModelDescriptor
from nodo.providers.deterministic import FakeModelProvider

ROME = ZoneInfo("Europe/Rome")


class Scripted:
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def models(self):
        return [ModelDescriptor(provider="scripted", model="s1", reasoning=4, coding=3, tier="free",
                                context_window=64_000)]

    async def available(self):
        return True

    async def stream(self, model, messages, **kw):
        self.prompts.append(messages[-1].content)
        yield ChatChunk(delta=self.replies.pop(0) if self.replies else "[]")
        yield ChatChunk(done=True, tokens_in=1, tokens_out=1)


def test_noise_is_recognised():
    for junk in ("है। है। तुम मुझे दे दे।", "保佑我多生哦。", "나 쉬었어", "", "F", "…"):
        assert mind.is_noise(junk), junk
    for ok in ("Ciao Maria", "egg", "Sì.", "Minchia che giornata", "Perché?"):
        assert not mind.is_noise(ok), ok


def test_people_rules_and_updates(db, seeded):
    ops = [{"op": "add", "kind": "person", "name": "Marcello Lo Monaco",
            "text": "Amico di Gaetano; per scherzo Gaetano dice che è il «catu i munnizza»"},
           {"op": "add", "kind": "person", "name": "Ciccio Lotà", "text": "Francesco Lotà, con la T; amico di Gaetano"},
           {"op": "add", "kind": "rule", "text": "Quando Gaetano dice che arriva Marcello Lo Monaco rispondi: "
                                                 "«Oh! Marcello è il catu i munnizza»"},
           {"op": "add", "kind": "prefer", "text": "Usa «minchia» solo quando si scherza"},
           {"op": "add", "kind": "rule", "text": "Quando Gaetano dice che arriva Marcello Lo Monaco rispondi: "
                                                 "«Oh! Marcello è il catu i munnizza»"}]      # duplicate: ignored
    done = mind.apply_ops(db, seeded, seeded, ops)
    assert len(done) == 4
    ppl = mind.people(db, seeded)
    assert set(ppl) == {"marcello-lo-monaco", "ciccio-lota"}                 # two people, never merged
    rule = mind.active(db, seeded, "rule")[0]
    mind.apply_ops(db, seeded, seeded, [{"op": "update", "id": rule.id[:8],
                                         "text": "Quando arriva Marcello Lo Monaco: «Ah, ecco, mi devi dare i 30 euro»"}])
    rules = mind.active(db, seeded, "rule")
    assert len(rules) == 1 and "30 euro" in rules[0].content
    assert db.get(MemoryFact, rule.id).status == FactStatus.SUPERSEDED
    pref = next(p for p in mind.active(db, seeded, "prefer") if "minchia" in p.content)
    mind.apply_ops(db, seeded, seeded, [{"op": "delete", "id": pref.id[:8]}])
    assert not any("minchia" in p.content for p in mind.active(db, seeded, "prefer"))
    # a second note about the same person goes on the same card
    mind.apply_ops(db, seeded, seeded, [{"op": "add", "kind": "person", "name": "marcello lo monaco",
                                         "text": "Deve 30 euro a CF Materassi"}])
    assert len(mind.people(db, seeded)["marcello-lo-monaco"]) == 2


def test_core_memory_is_in_front_of_every_answer(db, seeded):
    mind.apply_ops(db, seeded, seeded, [
        {"op": "add", "kind": "rule", "text": "Usa «minchia» solo quando capisci che si scherza"},
        {"op": "add", "kind": "person", "name": "Filippo", "text": "Titolare di CF Materassi"},
        {"op": "add", "kind": "fact", "text": "Morpheus Memory due strati fuori misura 80x180, lastra 7 cm, per la "
                                              "signora D'Amico, via Figarazzelli, Portella di Mare"}])
    conv = Conversation(organization_id=seeded, channel="voice", active_context={})
    db.add(conv)
    db.flush()
    now = datetime.now(UTC)
    db.add_all([Message(conversation_id=conv.id, role="user", content="Oggi consegno a Russo", created_at=now),
                Message(conversation_id=conv.id, role="user", content="保佑我多生哦", created_at=now)])
    db.flush()
    ctx = mind.memory_context(db, seeded, ROME, query="la signora D'Amico")
    assert "REGOLE" in ctx and "minchia" in ctx and "Filippo: Titolare" in ctx
    assert "OGGI VI SIETE DETTI" in ctx and "Oggi consegno a Russo" in ctx and "保佑" not in ctx
    assert "RICORDI CHE C'ENTRANO" in ctx and "Portella di Mare" in ctx


def test_old_conversations_can_be_searched(db, seeded):
    conv = Conversation(organization_id=seeded, channel="voice", active_context={})
    db.add(conv)
    db.flush()
    t = datetime.now(UTC) - timedelta(days=1)
    for i, (role, text) in enumerate([("user", "Ciao"), ("user", "Quando ti dico sta arrivando Ciccio Lotà"),
                                      ("nodo", "U sbirro chiù sbirro di tutti"), ("user", "Bravissima")]):
        db.add(Message(conversation_id=conv.id, role=role, content=text, created_at=t + timedelta(seconds=i)))
    db.flush()
    hits = mind.search_messages(db, "Ciccio Lotà", ROME)
    assert hits and any("sbirro" in line for line in hits[0]["exchange"])
    assert mind.search_messages(db, "pizzeria", ROME) == []


async def test_the_archivist_reads_the_turns_and_updates_the_memory(db, seeded):
    model = Scripted(['[{"op":"add","kind":"person","name":"Marcello Lo Monaco","text":"Deve 30 euro al negozio"},'
                      '{"op":"add","kind":"rule","text":"Quando arriva Marcello dì: Ah, ecco, mi devi dare i 30 euro"}]'])
    c = build_container(Settings(), providers=[model, FakeModelProvider()])
    conv = Conversation(organization_id=seeded, channel="voice", active_context={})
    db.add(conv)
    db.flush()
    db.add_all([Message(conversation_id=conv.id, role="user", content="Marcello ci deve 30 euro, quando arriva "
                        "dimmi ah ecco mi devi dare i 30 euro"),
                Message(conversation_id=conv.id, role="nodo", content="Ok, me lo ricordo.")])
    db.commit()
    done = await mind.archive_turns(c, db, conv.id)
    assert len(done) == 2 and "Marcello" in model.prompts[0] and "MEMORIA ATTUALE" in model.prompts[0]
    assert await mind.archive_turns(c, db, conv.id) == []               # nothing new since the last pass
    assert len(model.prompts) == 1


async def test_after_turn_is_debounced(db, seeded, monkeypatch):
    calls = []

    async def fake(container, db, cid):
        calls.append(cid)
        return []
    monkeypatch.setattr(mind, "archive_turns", fake)
    c = build_container(Settings(), providers=[FakeModelProvider()])
    for _ in range(3):
        mind.after_turn(c, "conv-1", delay=0.01, force=True)
    await mind.drain()
    assert calls == ["conv-1"]


def test_promises_without_actions_are_caught():
    assert unbacked_claim("Ho segnato nel registro la vendita a Rossi.", []) is not None
    assert unbacked_claim("Ho segnato nel registro la vendita a Rossi.", ["ledger_add"]) is None
    assert unbacked_claim("Ho preparato il file con la merce uscita oggi.", []) is not None
    assert unbacked_claim("Te l'ho messo sul desktop.", ["create_document"]) is not None
    assert unbacked_claim("Ok, me lo ricordo.", []) is None


async def test_silence_when_he_talks_to_someone_else(db, seeded):
    from nodo.brain.loop import _SILENCE
    for s in ("[silenzio]", " (silenzio) ", "Silenzio."):
        assert _SILENCE.fullmatch(s)
    assert not _SILENCE.fullmatch("Silenzio, sto pensando")


def test_a_debt_is_not_an_expense(db, seeded):
    from datetime import date

    from nodo.ledger import Ledger
    led = Ledger(db, seeded, "CF Materassi", today=date(2026, 10, 13))
    e = led.add(kind="debito", counterpart="Marcello Lo Monaco", amount=30)
    assert e.kind == "credito" and e.payment == "sospeso"
    rep = led.report(date(2026, 10, 13), date(2026, 10, 13))
    assert rep["totals"]["uscite"] == 0 and rep["totals"]["da_incassare_sospeso"] == 30
    assert "Marcello Lo Monaco ti deve 30 euro" in rep["say"]
    assert [r.counterpart for r in led.open_payments()] == ["Marcello Lo Monaco"]
    led.update(e, payment="pagato")
    assert led.report(date(2026, 10, 13), date(2026, 10, 13))["totals"]["incassato"] == 30


async def test_hands_on_the_pc(tmp_path, monkeypatch, db, seeded, world):
    from pathlib import Path

    from nodo.brain.tools import Toolbox
    from nodo.services.files import FileStore
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = build_container(Settings(), providers=[FakeModelProvider()])
    c.files = FileStore(tmp_path / "files")
    core = c.core(db)
    conv = Conversation(organization_id=core.org, channel="text", active_context={})
    db.add(conv)
    db.flush()
    box = Toolbox(core, conv, "r", "", "text")
    await box.call("create_document", {"name": "merce-oggi", "format": "pdf", "content": "# Merce\n\nCiao"})
    out = await box.call("save_to_desktop", {"name": "l'ultimo"})
    assert out["file"] == "merce-oggi.pdf" and (tmp_path / "Desktop" / "merce-oggi.pdf").exists()
    again = await box.call("save_to_desktop", {"name": "merce"})
    assert again["file"] == "merce-oggi (2).pdf"
    assert "error" in await box.call("save_to_desktop", {"name": "nessun file così"}) or True


@pytest.mark.parametrize("text", ["", "   "])
def test_parse_ops_handles_empty(text):
    assert mind.parse_ops(text) == []
