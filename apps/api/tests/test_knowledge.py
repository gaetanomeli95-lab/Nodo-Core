"""Personal knowledge: ChatGPT/Claude export parsing, local import + FTS search, honest distillation."""
from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator

from sqlalchemy import select

from nodo.app import build_container
from nodo.db.models import Document, FactStatus, KnowledgeSource, MemoryFact, Sensitivity
from nodo.knowledge import distill
from nodo.knowledge.importers import chunk_turns, parse_conversations, read_export
from nodo.knowledge.service import KnowledgeService
from nodo.night.notify import FakeNotifier
from nodo.providers.base import ChatChunk, ModelDescriptor
from nodo.tools.github import FakeGitHubConnector
from nodo.voice.providers import FakeSTT, FakeTTS


# ------------------------------------------------------------------------------------------------ fixtures
def chatgpt_export() -> list[dict]:
    """A conversation tree with a system node, a regenerated (dead) branch and a hidden message."""
    return [{
        "title": "Listino CF Materassi", "create_time": 1_759_000_000, "conversation_id": "cg-1",
        "current_node": "a2",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["sys"]},
            "sys": {"id": "sys", "parent": "root", "children": ["u1"],
                    "message": {"author": {"role": "system"}, "content": {"content_type": "text", "parts": [""]}}},
            "u1": {"id": "u1", "parent": "sys", "children": ["a1", "a1b"],
                   "message": {"author": {"role": "user"}, "create_time": 1_759_000_010,
                               "content": {"content_type": "text", "parts": ["Devo aggiornare il listino sul sito di CF Materassi, preferisco prezzi IVA inclusa."]}}},
            "a1": {"id": "a1", "parent": "u1", "children": [],
                   "message": {"author": {"role": "assistant"},
                               "content": {"content_type": "text", "parts": ["RISPOSTA SCARTATA"]}}},
            "a1b": {"id": "a1b", "parent": "u1", "children": ["h", "u2"],
                    "message": {"author": {"role": "assistant"}, "create_time": 1_759_000_020,
                                "content": {"content_type": "text", "parts": ["Ok, mettiamo i prezzi IVA inclusa."]}}},
            "h": {"id": "h", "parent": "a1b", "children": [],
                  "message": {"author": {"role": "user"}, "metadata": {"is_visually_hidden_from_conversation": True},
                              "content": {"content_type": "text", "parts": ["NASCOSTO"]}}},
            "u2": {"id": "u2", "parent": "a1b", "children": ["a2"],
                   "message": {"author": {"role": "user"}, "create_time": 1_759_000_030,
                               "content": {"content_type": "multimodal_text", "parts": [{"content_type": "image_asset_pointer"}, "Il materasso Comfort costa 499 euro."]}}},
            "a2": {"id": "a2", "parent": "u2", "children": [],
                   "message": {"author": {"role": "assistant"}, "create_time": 1_759_000_040,
                               "content": {"content_type": "code", "text": "<li>Comfort — 499 €</li>"}}},
        },
    }]


def claude_export() -> list[dict]:
    return [{"uuid": "cl-1", "name": "Prosperya CRM scope", "created_at": "2026-09-20T10:00:00Z",
             "chat_messages": [
                 {"sender": "human", "text": "Per Prosperya CRM ho deciso di rimandare il modulo fiscale a novembre.",
                  "created_at": "2026-09-20T10:00:05Z"},
                 {"sender": "assistant", "text": "", "created_at": "2026-09-20T10:00:09Z",
                  "content": [{"type": "text", "text": "Capito: modulo fiscale a novembre."}]},
             ]},
            {"uuid": "cl-empty", "name": "vuota", "created_at": "2026-09-21T10:00:00Z", "chat_messages": []}]


class ScriptedModel:
    """A tool-less model that answers every call with the given text (plays the distiller)."""
    name = "scripted"

    def __init__(self, answer: str):
        self.answer, self.calls = answer, 0

    def models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(provider=self.name, model="m", reasoning=3, coding=0, is_local=True, tier="local",
                                latency_class="low", max_sensitivity=Sensitivity.HIGHLY_SENSITIVE)]

    async def available(self) -> bool:
        return True

    async def stream(self, model, messages, *, temperature=0.3, max_tokens=800) -> AsyncIterator[ChatChunk]:
        self.calls += 1
        yield ChatChunk(delta=self.answer)
        yield ChatChunk(done=True, tokens_in=10, tokens_out=10)


def _container(settings, *providers):
    from nodo.providers.deterministic import FakeModelProvider
    return build_container(settings, providers=[*providers, FakeModelProvider()], github=FakeGitHubConnector(),
                           stt=FakeSTT(), tts=FakeTTS(), notifier=FakeNotifier())


# ------------------------------------------------------------------------------------------------ parsing
def test_chatgpt_parser_follows_the_active_branch_and_skips_hidden_or_system():
    convs = list(parse_conversations(chatgpt_export()))
    assert len(convs) == 1
    c = convs[0]
    assert c.kind == "chatgpt" and c.title == "Listino CF Materassi" and c.external_id == "cg-1"
    texts = [t.text for t in c.turns]
    assert "RISPOSTA SCARTATA" not in " ".join(texts) and "NASCOSTO" not in " ".join(texts)
    assert [t.role for t in c.turns] == ["user", "assistant", "user", "assistant"]
    assert texts[2] == "Il materasso Comfort costa 499 euro." and texts[3].startswith("<li>")
    assert c.created_at is not None and c.created_at.year == 2025


def test_claude_parser_reads_content_blocks_and_drops_empty_conversations():
    convs = list(parse_conversations(claude_export()))
    assert [c.external_id for c in convs] == ["cl-1"]
    assert convs[0].turns[1].text == "Capito: modulo fiscale a novembre."
    assert convs[0].created_at is not None and convs[0].created_at.month == 9


def test_zip_export_is_unpacked_and_bad_files_are_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("chat.html", "<html>")
        z.writestr("conversations.json", json.dumps(claude_export()))
    assert len(read_export(buf.getvalue(), "claude.zip")) == 1
    import pytest

    from nodo.knowledge.importers import UnknownExport
    with pytest.raises(UnknownExport):
        read_export(b'{"hello": "world"}', "x.json")
    with pytest.raises(UnknownExport):
        read_export(b"not json", "x.json")


def test_chunking_keeps_turns_together_and_cuts_long_ones():
    convs = list(parse_conversations(chatgpt_export()))
    chunks = chunk_turns(convs[0].turns, size=120)
    assert len(chunks) >= 2 and all(len(t) <= 125 for t, _ in chunks)
    assert chunks[0][0].startswith("Gaetano: ")
    assert chunks[0][1] is not None  # time of the first turn in the slice


# ------------------------------------------------------------------------------------------------ import + search
def test_import_is_idempotent_and_searchable(db, seeded):
    svc = KnowledgeService(db, seeded)
    convs = read_export(json.dumps(chatgpt_export()).encode(), "conversations.json")
    src, st = svc.import_conversations(convs, kind="chatgpt", label="conversations.json")
    assert st.new == 1 and st.chunks >= 1 and src.status == "imported"
    docs = db.scalars(select(Document).where(Document.kind == "chat:chatgpt")).all()
    assert len(docs) == 1 and docs[0].uri == "chatgpt:cg-1" and docs[0].sensitivity == Sensitivity.CONFIDENTIAL

    again, st2 = svc.import_conversations(convs, kind="chatgpt", label="conversations.json")
    assert st2.unchanged == 1 and st2.new == 0 and st2.chunks == 0
    assert len(db.scalars(select(Document).where(Document.kind == "chat:chatgpt")).all()) == 1

    hits = svc.search("prezzi iva inclusa")
    assert hits and hits[0]["title"] == "Listino CF Materassi" and "IVA" in hits[0]["snippet"]
    assert hits[0]["when"] is not None
    assert svc.search("zzzz-nonexistent") == []
    assert svc.chunk_text(hits[0]["chunk_id"]).startswith("Gaetano:")
    assert svc.overview()["conversations"] == 1

    # a grown conversation is re-imported: chunks replaced, document kept
    convs[0].turns.append(convs[0].turns[0])
    _, st3 = svc.import_conversations(convs, kind="chatgpt", label="conversations.json")
    assert st3.updated == 1
    assert len(db.scalars(select(Document).where(Document.kind == "chat:chatgpt")).all()) == 1


def test_removing_a_source_archives_its_facts(db, seeded):
    svc = KnowledgeService(db, seeded)
    src, _ = svc.import_conversations(read_export(json.dumps(claude_export()).encode()), kind="claude", label="c")
    doc = db.scalars(select(Document).where(Document.kind == "chat:claude")).one()
    from nodo.memory.service import MemoryService
    MemoryService(db, seeded).remember("user", "u", "fatto distillato", source_type="document", source_id=doc.id,
                                        status=FactStatus.INFERRED)
    assert svc.remove_source(src.id)
    assert db.get(KnowledgeSource, src.id) is None
    f = db.scalars(select(MemoryFact).where(MemoryFact.content == "fatto distillato")).one()
    assert f.status == FactStatus.ARCHIVED and f.valid_until is not None
    assert svc.overview()["conversations"] == 0


# ------------------------------------------------------------------------------------------------ distillation
def test_parse_facts_is_lenient():
    assert distill.parse_facts("no json here") == []
    out = distill.parse_facts('Ecco:\n[{"about": "Gaetano", "fact": " usa Vercel ", "kind": "Preferenza", '
                              '"confidence": "0.9"}, {"fact": ""}, "junk", {"about": "X", "fact": "y"}]')
    assert out[0] == {"about": "Gaetano", "fact": "usa Vercel", "kind": "preferenza", "confidence": 0.9}
    assert out[1]["confidence"] == 0.7 and len(out) == 2


async def test_distillation_saves_facts_with_provenance_and_skips_duplicates(engine, settings, seeded):
    answer = json.dumps([
        {"about": "Gaetano", "fact": "Preferisce mostrare i prezzi IVA inclusa sui siti", "kind": "preferenza",
         "confidence": 0.9},
        {"about": "CF Materassi", "fact": "Il materasso Comfort costa 499 euro", "kind": "fatto", "confidence": 0.8},
        {"about": "Gaetano", "fact": "Ipotesi debole", "kind": "fatto", "confidence": 0.2},
    ])
    model = ScriptedModel(answer)
    c = _container(settings, model)
    from nodo.db.session import session_scope
    with session_scope() as db:
        org = c.core(db).org
        src, _ = KnowledgeService(db, org).import_conversations(
            read_export(json.dumps(chatgpt_export()).encode()), kind="chatgpt", label="conversations.json")
        src_id = src.id
    stats = await distill.run(c, src_id)
    assert stats["distilled"] == 1 and stats["facts"] == 2 and stats["skipped"] == 1 and model.calls == 1
    with session_scope() as db:
        src = db.get(KnowledgeSource, src_id)
        assert src.status == "done" and src.stats["remaining"] == 0
        doc = db.scalars(select(Document).where(Document.kind == "chat:chatgpt")).one()
        facts = db.scalars(select(MemoryFact).where(MemoryFact.source_id == doc.id)).all()
        assert {f.content for f in facts} == {"Preferisce mostrare i prezzi IVA inclusa sui siti",
                                              "Il materasso Comfort costa 499 euro"}
        assert all(f.status == FactStatus.INFERRED and f.source_type == "document" for f in facts)
        by_content = {f.content: f for f in facts}
        assert by_content["Preferisce mostrare i prezzi IVA inclusa sui siti"].subject_type == "user"
        assert by_content["Preferisce mostrare i prezzi IVA inclusa sui siti"].layer == "preference"
        assert by_content["Il materasso Comfort costa 499 euro"].subject_type == "project"   # resolved by name
        assert "✓" in doc.summary
    # a second run has nothing left to do and never duplicates
    stats2 = await distill.run(c, src_id)
    assert stats2.get("distilled", 0) == 1 and model.calls == 1
    with session_scope() as db:
        assert len(db.scalars(select(MemoryFact).where(MemoryFact.source_type == "document")).all()) == 2


async def test_distillation_without_a_real_model_stops_honestly(engine, settings, seeded):
    c = _container(settings)   # deterministic provider only
    from nodo.db.session import session_scope
    with session_scope() as db:
        org = c.core(db).org
        src, _ = KnowledgeService(db, org).import_conversations(
            read_export(json.dumps(claude_export()).encode()), kind="claude", label="c")
        src_id = src.id
    await distill.run(c, src_id)
    with session_scope() as db:
        src = db.get(KnowledgeSource, src_id)
        assert src.status == "no_model" and "modello" in (src.note or "")
        assert not db.scalars(select(MemoryFact).where(MemoryFact.source_type == "document")).all()


# ------------------------------------------------------------------------------------------------ API + brain tool
def test_knowledge_api_import_search_and_delete(client):
    r = client.post("/api/v1/knowledge/import", data={"distill_now": "false"},
                    files={"file": ("conversations.json", json.dumps(claude_export()).encode(), "application/json")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["import"]["new"] == 1 and body["distilling"] is False and body["source"]["kind"] == "claude"
    sid = body["source"]["id"]

    assert client.post("/api/v1/knowledge/import", data={"distill_now": "false"},
                       files={"file": ("x.json", b'{"a": 1}', "application/json")}).status_code == 400

    ov = client.get("/api/v1/knowledge").json()
    assert ov["conversations"] == 1 and ov["sources"][0]["id"] == sid

    hits = client.get("/api/v1/knowledge/search", params={"q": "modulo fiscale novembre"}).json()["results"]
    assert hits and hits[0]["source"] == "claude"

    assert client.post(f"/api/v1/knowledge/sources/{sid}/stop").json()["stopped"]
    assert client.delete(f"/api/v1/knowledge/sources/{sid}").json()["deleted"]
    assert client.get("/api/v1/knowledge").json()["conversations"] == 0
    assert client.delete(f"/api/v1/knowledge/sources/{sid}").status_code == 404


async def test_brain_tool_searches_imported_conversations(core, db, seeded):
    from nodo.brain.tools import Toolbox
    conv = core._conversation(None, "text")
    box = Toolbox(core, conv, "rid", "ricordi il listino?")
    empty = await box.call("search_knowledge", {"query": "listino"})
    assert empty["results"] == [] and "importato" in empty["note"]
    KnowledgeService(db, seeded).import_conversations(read_export(json.dumps(chatgpt_export()).encode()),
                                                      kind="chatgpt", label="c")
    out = await box.call("search_knowledge", {"query": "listino materassi"})
    assert out["results"] and out["results"][0]["title"] == "Listino CF Materassi"
    full = await box.call("read_knowledge", {"chunk_id": out["results"][0]["chunk_id"]})
    assert "IVA inclusa" in full["text"]
    assert "search_knowledge" in (await box.call("recall", {"query": "xyzzy quantum"}))["note"]
