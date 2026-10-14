from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from nodo.brain.loop import Brain
from nodo.brain.persona import build_live_persona
from nodo.brain.quick_read import match_read
from nodo.brain.tools import recent_history
from nodo.db.models import MemoryFact, Message
from nodo.db.session import session_scope
from nodo.ledger import Ledger
from nodo.memory.mind import memory_context
from nodo.providers.base import ToolTurn
from nodo.voice.runtime import VoiceRuntime


@pytest.mark.parametrize(("text", "expected"), [
    ("Maria, quali sono i sospesi?", ("ledger_open", {})),
    ("Elenca tutti i pagamenti sospesi", ("ledger_open", {})),
    ("Chi mi deve pagare?", ("ledger_open", {})),
    ("Quanto devo ancora incassare?", ("ledger_open", {})),
    ("Fammi il riepilogo di oggi", ("ledger_report", {"day": "oggi"})),
    ("Resoconto di ieri, grazie", ("ledger_report", {"day": "ieri"})),
])
def test_exact_read_requests(text, expected):
    assert match_read(text) == expected


@pytest.mark.parametrize("text", ["Segna i sospesi come pagati", "I sospesi di Marcello",
                                 "Quali sono i sospesi e manda un messaggio", "Non mostrarmi i sospesi",
                                 "Sì", "Quanto mi deve Marcello?", "Riepilogo oggi e cancella ieri"])
def test_ambiguous_or_mutating_requests_stay_with_the_brain(text):
    assert match_read(text) is None


async def test_register_read_uses_fresh_data_without_model_or_snapshot(core, db, monkeypatch):
    def no_snapshot(*a, **kw):
        pytest.fail("a simple read must not build the full brain prompt")
    monkeypatch.setattr(Brain, "snapshot", no_snapshot)
    ledger = Ledger(db, core.org, core.settings.ledger_business)
    row = ledger.add(kind="credito", counterpart="Marcello", item="Ordine", amount=30)
    out = await core.run_once("Maria, quali sono i sospesi?")
    assert out.provider == "nodo" and out.model == "register-read"
    assert "Marcello" in out.text and "30" in out.text
    ledger.update(row, payment="pagato")
    db.flush()
    out = await core.run_once("Maria, quali sono i sospesi?", out.conversation_id)
    assert "non risultano pagamenti sospesi" in out.text


def test_memory_and_snapshot_keep_rules_with_a_large_archive(core, db):
    rule = "Quando arriva Marcello rispondi: Benvenuto Marcello, ti aspettavo!"
    facts = [MemoryFact(organization_id=core.org, layer="rule", content=rule,
                        subject_type="user", subject_id="gaetano", source_type="manual")]
    facts += [MemoryFact(organization_id=core.org, layer="preference", content=f"Copertina TikTok {i}: " + "x" * 400,
                         subject_type="user", subject_id="gaetano", source_type="chatgpt_import") for i in range(380)]
    facts += [MemoryFact(organization_id=core.org, layer="relationship", content="Cliente " + "y" * 450,
                         subject_type="person", subject_id=f"persona-{i}", predicate=f"Persona {i}",
                         source_type="manual") for i in range(35)]
    facts.append(MemoryFact(organization_id=core.org, layer="relationship", content="DETTAGLIO_IMPORTATO_ESTRANEO",
                            subject_type="person", subject_id="persona-34", source_type="chatgpt_import"))
    db.add_all(facts)
    db.flush()
    ctx = memory_context(db, core.org, UTC, query="arriva Marcello")
    assert len(ctx) <= 6000 and rule in ctx
    assert "DETTAGLIO_IMPORTATO_ESTRANEO" not in ctx and "Copertina TikTok" not in ctx
    assert len(list(db.scalars(select(MemoryFact)))) >= len(facts)   # budgeting never deletes memory
    ledger = Ledger(db, core.org, core.settings.ledger_business)
    for i in range(15):
        ledger.add(kind="credito", counterpart=f"Cliente {i}", item="Ordine " * 100, amount=30)
    brain = Brain(core, core._conversation(None, "voice"), "large", "voice")
    snapshot = brain.snapshot("arriva Marcello")
    assert len(snapshot) <= 10000 and rule in snapshot
    live = build_live_persona(core.settings, snapshot, delegated=True)
    assert rule in live and "[silenzio]" not in live
    if "Pagamenti sospesi" in live:
        assert live.index(rule) < live.index("Pagamenti sospesi")


def test_relevant_old_rule_survives_many_new_rules(core, db):
    old = "Quando arriva Marcello rispondi: Finalmente Marcello!"
    now = datetime.now(UTC)
    db.add(MemoryFact(organization_id=core.org, layer="rule", content=old,
                      subject_type="user", subject_id="gaetano", source_type="manual",
                      recorded_at=now - timedelta(days=2)))
    for i in range(50):
        db.add(MemoryFact(organization_id=core.org, layer="rule", content=f"Progetto {i} " + "z" * 200,
                          subject_type="user", subject_id="gaetano", source_type="manual", recorded_at=now))
    db.flush()
    ctx = memory_context(db, core.org, UTC, query="arriva Marcello")
    assert old in ctx and len(ctx) <= 6000


def test_history_is_bounded_without_dropping_messages_with_no_request_id(core, db):
    conv = core._conversation(None, "voice")
    now = datetime.now(UTC)
    for i in range(16):
        db.add(Message(conversation_id=conv.id, role="user" if i % 2 == 0 else "nodo",
                       content=f"messaggio-{i}: " + "x" * 30000, created_at=now + timedelta(seconds=i)))
    db.flush()
    history = recent_history(db, conv.id, 16)
    assert sum(len(m["content"]) for m in history) <= 8000
    assert history[0]["role"] == "user"
    assert history[-2]["content"].startswith("messaggio-14:")
    assert history[-1]["content"].startswith("messaggio-15:")
    assert len(list(db.scalars(select(Message).where(Message.conversation_id == conv.id)))) == 16


async def test_directed_request_gets_a_clarification_instead_of_silence(core, monkeypatch):
    async def complete(*a, **kw):
        return ToolTurn("[silenzio]")
    monkeypatch.setattr(core.router, "complete_tools", complete)
    brain = Brain(core, core._conversation(None, "voice"), "directed", "voice", directed=True)
    events = [e async for e in brain.run("Mi aiuti con questa cosa?")]
    assert not brain.silent and "Me la ripeti" in brain.text
    assert all("[silenzio]" not in e.get("text", "") for e in events)


def runtime(container):
    async def emit(event):
        pass
    return VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)


async def test_live_keeps_exact_read_despite_model_paraphrase(container, engine, monkeypatch):
    def no_snapshot(*a, **kw):
        pytest.fail("the paraphrase must not disable the exact-read shortcut")
    monkeypatch.setattr(Brain, "snapshot", no_snapshot)
    rt = runtime(container)
    try:
        answer = await rt._live_think({"richiesta": "Elenca i clienti che devono ancora pagare"}, [],
                                      heard="Maria, quali sono i sospesi?")
        assert "non risultano pagamenti sospesi" in answer["risposta"]
    finally:
        await rt.close()


async def test_repeated_pensa_reuses_one_result_only_within_the_same_turn(container, engine, monkeypatch):
    rt = runtime(container)
    started, release = asyncio.Event(), asyncio.Event()
    executions, responses = [], []

    async def think(args, bindings, **kw):
        executions.append(args)
        started.set()
        await release.wait()
        return {"risposta": "Trenta euro"}

    async def respond(values):
        responses.extend(values)

    monkeypatch.setattr(rt, "_live_think", think)
    rt._live = SimpleNamespace(tool_response=respond)
    rt._in_text = "Quanto devo incassare?"
    call = {"kind": "tool_call", "name": "pensa", "args": {"richiesta": rt._in_text}}
    try:
        await rt._on_live_event({**call, "id": "one"})
        await asyncio.wait_for(started.wait(), 1)
        await rt._on_live_event({**call, "id": "two"})
        release.set()
        await asyncio.gather(*rt._live_tools.values())
        assert len(executions) == 1 and {r[0] for r in responses} == {"one", "two"}
        rt.vs.turn_number += 1
        await rt._on_live_event({**call, "id": "three"})
        await asyncio.gather(*rt._live_tools.values())
        assert len(executions) == 2  # no stale balance cached across user turns
    finally:
        rt._live = None
        await rt.close()


async def test_old_result_cannot_speak_after_interruption(container, engine, monkeypatch):
    rt = runtime(container)
    responses = []

    async def think(args, bindings, **kw):
        rt._gen += 1
        return {"risposta": "old result"}

    async def respond(values):
        responses.extend(values)

    monkeypatch.setattr(rt, "_live_think", think)
    rt._live = SimpleNamespace(tool_response=respond)
    try:
        await rt._live_tool_calls([{"id": "old", "name": "pensa", "args": {}, "generation": rt._gen}])
        assert responses == []
    finally:
        rt._live = None
        await rt.close()


async def test_new_question_cancels_old_pensa_instead_of_queuing(container, engine, monkeypatch):
    rt = runtime(container)
    started, cancelled = asyncio.Event(), asyncio.Event()
    responses = []

    async def think(args, bindings, **kw):
        if args["richiesta"] == "prima":
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return {"risposta": "risposta nuova"}

    async def respond(values):
        responses.extend(values)

    monkeypatch.setattr(rt, "_live_think", think)
    rt._live = SimpleNamespace(tool_response=respond)
    try:
        for cid, question in [("old", "prima"), ("new", "seconda")]:
            rt._in_text = question
            await rt._on_live_event({"kind": "tool_call", "id": cid, "name": "pensa",
                                     "args": {"richiesta": question}})
            if cid == "old":
                await asyncio.wait_for(started.wait(), 1)
        await asyncio.gather(*rt._live_tools.values())
        assert cancelled.is_set()
        assert any(r[0] == "new" and r[2]["risposta"] == "risposta nuova" for r in responses)
        assert not any(r[0] == "old" and r[2].get("risposta") for r in responses)
    finally:
        rt._live = None
        await rt.close()


@pytest.mark.parametrize("native", [False, True])
async def test_both_manual_and_native_interrupt_cancel_pensa(container, engine, monkeypatch, native):
    rt = runtime(container)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def think(args, bindings, **kw):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(rt, "_live_think", think)
    rt._live = SimpleNamespace()
    try:
        await rt._on_live_event({"kind": "tool_call", "id": "slow", "name": "pensa",
                                 "args": {"richiesta": "una ricerca lunga"}})
        await asyncio.wait_for(started.wait(), 1)
        if native:
            await rt._on_live_event({"kind": "interrupted"})
        else:
            await rt.interrupt()
        await asyncio.wait_for(cancelled.wait(), 1)
        await asyncio.gather(*rt._live_tools.values(), return_exceptions=True)
    finally:
        rt._live = None
        await rt.close()
