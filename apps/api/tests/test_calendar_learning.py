"""Calendar (secret iCal link), certificate / domain watching, and learning from finished conversations."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from nodo.app import build_container
from nodo.config import Settings
from nodo.db.models import Conversation, FactStatus, MemoryFact, Message
from nodo.memory import learn
from nodo.night.notify import FakeNotifier
from nodo.night.scheduler import NightScheduler
from nodo.providers.base import ChatChunk, ModelDescriptor
from nodo.providers.deterministic import FakeModelProvider
from nodo.services import calendar as cal
from nodo.services import expiry
from tests.test_brain_extra import box  # noqa: F401  (fixture)

ROME = ZoneInfo("Europe/Rome")

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a1
SUMMARY:Call con Bar Sole
DTSTART;TZID=Europe/Rome:20261014T150000
DTEND;TZID=Europe/Rome:20261014T160000
LOCATION:Zoom\\, link in mail
END:VEVENT
BEGIN:VEVENT
UID:w1
SUMMARY:Riunione del lunedì
DTSTART;TZID=Europe/Rome:20261005T093000
DTEND;TZID=Europe/Rome:20261005T100000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE;TZID=Europe/Rome:20261014T093000
END:VEVENT
BEGIN:VEVENT
UID:w1
RECURRENCE-ID;TZID=Europe/Rome:20261019T093000
SUMMARY:Riunione spostata
DTSTART;TZID=Europe/Rome:20261019T170000
DTEND;TZID=Europe/Rome:20261019T180000
END:VEVENT
BEGIN:VEVENT
UID:d1
SUMMARY:Scadenza F24
DTSTART;VALUE=DATE:20261016
END:VEVENT
BEGIN:VEVENT
UID:m1
SUMMARY:Fattura mensile
DTSTART;TZID=Europe/Rome:20260901T120000
DTEND;TZID=Europe/Rome:20260901T121500
RRULE:FREQ=MONTHLY;BYDAY=-1FR
END:VEVENT
BEGIN:VEVENT
UID:x1
SUMMARY:Annullato
STATUS:CANCELLED
DTSTART;TZID=Europe/Rome:20261015T100000
END:VEVENT
END:VCALENDAR
"""


def _titles(events):
    return [(e.start.astimezone(ROME).strftime("%m-%d %H:%M"), e.title) for e in events]


def test_calendar_expands_recurrence_exdates_overrides_and_all_day():
    lo = datetime(2026, 10, 12, tzinfo=ROME)
    got = _titles(cal.parse_events(ICS, lo, lo + timedelta(days=21), ROME))
    assert ("10-14 15:00", "Call con Bar Sole") in got
    assert ("10-14 09:30", "Riunione del lunedì") not in got            # EXDATE
    assert ("10-12 09:30", "Riunione del lunedì") in got and ("10-21 09:30", "Riunione del lunedì") in got
    assert ("10-19 09:30", "Riunione del lunedì") not in got            # replaced by the override...
    assert ("10-19 17:00", "Riunione spostata") in got                  # ...which is shown
    assert ("10-16 00:00", "Scadenza F24") in got
    assert ("10-30 12:00", "Fattura mensile") in got                    # last Friday of the month
    assert not any(t == "Annullato" for _, t in got)
    assert got == sorted(got)


def test_calendar_text_escapes_and_event_dict():
    lo = datetime(2026, 10, 14, tzinfo=ROME)
    call = next(e for e in cal.parse_events(ICS, lo, lo + timedelta(days=1), ROME) if e.title.startswith("Call"))
    assert call.location == "Zoom, link in mail" and call.as_dict(ROME)["from"] == "15:00"


async def test_calendar_tool_reads_the_link_and_never_leaks_it(box, db):  # noqa: F811
    assert "non è collegato" in (await box.call("calendar_events", {}))["error"]
    box.settings.calendar_ics_url = "https://calendar.example/secret-token-123/basic.ics"

    async def fake_fetch(url, days, tz, **kw):
        return cal.parse_events(ICS, datetime(2026, 10, 12, tzinfo=tz), datetime(2026, 10, 20, tzinfo=tz), tz)

    import nodo.services.calendar as mod
    real = mod.fetch_events
    mod.fetch_events = fake_fetch
    try:
        out = await box.call("calendar_events", {"days": 7})
    finally:
        mod.fetch_events = real
    assert out["total"] >= 4 and any(e["title"] == "Scadenza F24" and e["all_day"] for e in out["events"])

    async def boom(url, days, tz, **kw):
        raise httpx.ConnectError("no")
    mod.fetch_events = boom
    try:
        bad = await box.call("calendar_events", {})
    finally:
        mod.fetch_events = real
    assert "error" in bad and "secret-token" not in str(bad)


async def test_fetch_events_downloads_the_ics():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ICS)))
    evs = await cal.fetch_events("webcal://calendar.example/x.ics", 7, ROME,
                                 start=datetime(2026, 10, 14, 8, tzinfo=ROME), client=client)
    assert any(e.title == "Call con Bar Sole" for e in evs)


async def test_domain_expiry_from_rdap_and_shared_hosts_skipped():
    def handler(request):
        assert request.url.path.endswith("/example.it")
        return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
                                                    {"eventAction": "expiration", "eventDate": "2027-03-04T10:00:00Z"}]})
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await expiry.domain_expiry("www.example.it", client=c) == "2027-03-04"
    assert await expiry.domain_expiry("mio-sito.vercel.app") is None


async def test_certificate_problems_are_reported_not_raised():
    days, why = await expiry.certificate_days("localhost", port=1, timeout=2)
    assert days is None and "collegarmi" in why


async def test_watch_warns_about_a_certificate_that_is_about_to_expire(db, seeded, world, monkeypatch):
    c = build_container(Settings(), providers=[FakeModelProvider()], vercel=world.vercel, repo=world.github,
                        prober=world.prober, notifier=FakeNotifier())
    await c.night_shift(db).discover()
    db.add(Conversation(organization_id=seeded, channel="text", active_context={}))
    db.commit()

    async def fake_days(host, port=443, timeout=8.0):
        return (7, "") if "prosperya" in host else (80, "")
    monkeypatch.setattr(expiry, "certificate_days", fake_days)
    sched = NightScheduler(c)
    out = await sched.watch_expiry()
    print(out)
    assert out and all("7 giorni" in w for w in out)
    assert any("scade tra 7 giorni" in m for m in c.notifier.sent)


class _Thinker:
    """A scripted model good enough to be picked for learning (the grounded fallback is not)."""
    name = "thinker"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def models(self):
        return [ModelDescriptor(provider="thinker", model="t1", reasoning=3, coding=3, tier="free",
                                context_window=32_000)]

    async def available(self):
        return True

    async def stream(self, model, messages, **kw):
        self.prompts.append(messages[-1].content)
        yield ChatChunk(delta=self.reply)
        yield ChatChunk(done=True, tokens_in=10, tokens_out=10)


async def test_nodo_keeps_the_lasting_facts_of_a_finished_conversation(db, seeded):
    model = _Thinker('Ecco: ["Gaetano preferisce ricevere il briefing alle 8", "Il cliente Timilia paga a 60 giorni", '
                     '"ok"]')
    c = build_container(Settings(), providers=[model, FakeModelProvider()])
    old = datetime.now(UTC) - timedelta(hours=2)
    conv = Conversation(organization_id=seeded, channel="text", active_context={})
    db.add(conv)
    db.flush()
    db.add_all([Message(conversation_id=conv.id, role="user", content="Timilia mi paga sempre a 60 giorni", created_at=old),
                Message(conversation_id=conv.id, role="nodo", content="Annotato.", created_at=old)])
    db.flush()
    saved = await learn.learn_once(c, db)
    assert saved == ["Gaetano preferisce ricevere il briefing alle 8", "Il cliente Timilia paga a 60 giorni"]
    assert "Timilia mi paga" in model.prompts[0]
    rows = db.scalars(select(MemoryFact).where(MemoryFact.source_type == "conversation",
                                               MemoryFact.content.like("%Timilia%"))).all()
    assert len(rows) == 1 and rows[0].status == FactStatus.INFERRED
    assert await learn.learn_once(c, db) == []                      # already learned: nothing twice
    model.reply = '["Il cliente Timilia paga a 60 giorni."]'
    db.add(Message(conversation_id=conv.id, role="user", content="E lo ripeto: 60 giorni", created_at=old))
    db.flush()
    assert await learn.learn_once(c, db) == []                      # known fact is not duplicated


async def test_learning_waits_for_a_quiet_conversation_and_for_a_model(db, seeded):
    c = build_container(Settings(), providers=[FakeModelProvider()])
    conv = Conversation(organization_id=seeded, channel="text", active_context={})
    db.add(conv)
    db.flush()
    db.add(Message(conversation_id=conv.id, role="user", content="Ciao, ricorda che amo il caffè lungo"))
    db.flush()
    assert await learn.learn_once(c, db) == []                      # still talking
    assert await learn.learn_once(c, db, now=datetime.now(UTC) + timedelta(hours=1)) == []   # no model: not marked
    assert "learned_until" not in (conv.active_context or {})


def test_parse_facts_is_forgiving():
    assert learn.parse_facts("niente") == [] and learn.parse_facts("[]") == []
    assert learn.parse_facts('```json\n["Gaetano lavora di notte spesso", 3, "x"]\n```') == ["Gaetano lavora di notte spesso"]


@pytest.mark.parametrize("bad", ["{not json", "[1,2", None])
def test_parse_facts_ignores_garbage(bad):
    assert learn.parse_facts(bad) == []
