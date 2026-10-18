"""Predictive routines: habits are mined from the real history and shown only when the hour fits."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from nodo.db.models import Message
from nodo.greeting import build_greeting
from nodo.memory import mind, routines

ROME = ZoneInfo("Europe/Rome")


def _say(db, conv_id: str, text: str, when: datetime) -> Message:
    m = Message(conversation_id=conv_id, role="user", content=text)
    db.add(m)
    db.flush()
    m.created_at = when                       # stored UTC; override the insert default after flush
    db.flush()
    return m


def _at(day_offset: int, hour: int, minute: int = 10) -> datetime:
    """A past moment, `day_offset` days ago, at `hour` local time in Rome."""
    now = datetime.now(ROME)
    local = (now - timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(UTC)


def test_signature_normalizes_and_filters():
    assert routines.signature("Maria, riepilogo del registro") == routines.signature("riepilogo del registro")
    assert routines.signature("Allora Maria fammi il riepilogo del registro dai") == \
        routines.signature("fammi il riepilogo del registro")
    for junk in ("sì", "ok", "", "है। है। तुम", "a"):
        assert routines.signature(junk) is None


def test_mine_finds_recurring_requests(db, seeded):
    conv = _conversation(db, seeded)
    for day in (1, 4, 9, 12):                                  # 4 evenings, different days
        _say(db, conv.id, "Maria, fammi il riepilogo del registro", _at(day, 17))
    _say(db, conv.id, "che tempo fa domani", _at(2, 21))       # one-off: never a routine
    for _ in range(5):
        _say(db, conv.id, "accendi il computer", _at(2, 8))    # same-day burst is not a habit either
    found = routines.mine(db, seeded, ROME)
    assert len(found) == 1
    r = found[0]
    assert "riepilogo" in r["signature"] and r["count"] == 4 and r["days"] == 4
    assert r["band"] <= 17 < r["band"] + routines.BAND_H


def test_learn_persists_and_refreshes_routine_facts(db, seeded):
    conv = _conversation(db, seeded)
    for day in (1, 3, 8):
        _say(db, conv.id, "mi apri il programma delle vendite", _at(day, 9))
    n = routines.learn(db, seeded, ROME, "gaetano")
    assert n == 1
    facts = mind.active(db, seeded, "routine")
    assert len(facts) == 1 and "tra le 08 e le 11" in facts[0].content or "tra le" in facts[0].content
    first_id = facts[0].id
    _say(db, conv.id, "mi apri il programma delle vendite", _at(0, 9, 30))
    assert routines.learn(db, seeded, ROME, "gaetano") == 1
    facts = mind.active(db, seeded, "routine")
    assert len(facts) == 1 and facts[0].id == first_id         # refreshed in place, no duplicates
    assert "(4 volte" in facts[0].content


def test_anticipations_respect_the_hour(db, seeded):
    conv = _conversation(db, seeded)
    for day in (1, 3, 8):
        _say(db, conv.id, "mi apri il programma delle vendite", _at(day, 9))
    routines.learn(db, seeded, ROME, "gaetano")
    facts = mind.active(db, seeded, "routine")
    assert routines.anticipations(facts, 9)
    assert routines.anticipations(facts, 8)                    # -1h grace
    assert not routines.anticipations(facts, 23)               # wrong time of day: stay silent


def test_memory_context_surfaces_habits(db, seeded):
    conv = _conversation(db, seeded)
    for day in (1, 3, 8):
        _say(db, conv.id, "fammi il riepilogo del registro", _at(day, datetime.now(ROME).hour))
    routines.learn(db, seeded, ROME, "gaetano")
    ctx = mind.memory_context(db, seeded, ROME, query="", owner="Gaetano", assistant="Maria")
    assert "ABITUDINI A QUESTA ORA" in ctx
    assert "riepilogo del registro" in ctx
    assert "probabili bisogni, non certezze" in ctx            # framed as prediction, never as fact


def test_greeting_anticipates_the_habit(db, seeded, settings):
    conv = _conversation(db, seeded)
    for day in (1, 3, 8):
        _say(db, conv.id, "fammi il riepilogo del registro", _at(day, datetime.now(ROME).hour))
    routines.learn(db, seeded, ROME, "gaetano")
    g = build_greeting(db, seeded, settings)
    assert "di solito mi chiedi" in g["text"]
    assert "riepilogo del registro" in g["text"]


def _conversation(db, org: str):
    from nodo.db.models import Conversation
    c = Conversation(organization_id=org, channel="text", active_context={})
    db.add(c)
    db.flush()
    return c
