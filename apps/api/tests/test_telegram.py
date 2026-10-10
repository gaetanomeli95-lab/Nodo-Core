"""Telegram both ways: owner-only, buttons bound to one approval, "sì"/"no", commands, questions, voice notes."""
from __future__ import annotations

import json
import time

import httpx
from sqlalchemy import select

from nodo.channels.telegram import FakeTelegramApi, TelegramApi, TelegramChannel, chunks
from nodo.db.models import Approval, FixProposal
from nodo.night.notify import TelegramNotifier
from nodo.voice.base import Transcript
from tests.test_night import baseline, drain_background, make_container

OWNER = "4242"


def msg(text: str, chat: str = OWNER, age: int = 0, uid: int = 1) -> dict:
    return {"update_id": uid, "message": {"message_id": uid, "date": int(time.time()) - age, "text": text,
                                          "chat": {"id": int(chat), "type": "private"}}}


def tap(data: str, chat: str = OWNER, uid: int = 50) -> dict:
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": data,
                                                 "message": {"message_id": 7, "text": "Da decidere: …",
                                                             "chat": {"id": int(chat)}}}}


async def night(db, world, settings):
    c = make_container(world, settings)
    await baseline(db, c)
    world.break_things()
    await c.night_shift(db).run_pass("night", "test")
    db.commit()
    api = FakeTelegramApi()
    return c, api, TelegramChannel(c, api, OWNER)


async def test_strangers_are_ignored_and_old_messages_skipped(db, seeded, world, settings):
    c, api, ch = await night(db, world, settings)
    await ch.handle_update(msg("cosa hai fatto stanotte?", chat="999"))
    await ch.handle_update(msg("cosa hai fatto stanotte?", age=3600))
    await ch.handle_update(tap(f"a:{db.scalars(select(FixProposal)).one().approval_id}:y", chat="999"))
    assert api.sent == [] and api.answers == []


async def test_button_publishes_exactly_that_fix(db, seeded, world, settings):
    c, api, ch = await night(db, world, settings)
    fix = db.scalars(select(FixProposal)).one()
    await ch.handle_update(tap(f"a:{fix.approval_id}:y"))
    await drain_background()
    db.expire_all()
    assert db.get(FixProposal, fix.id).status == "merged"
    assert api.answers and "PR" in api.answers[0] and "→" in api.edits[0]["text"]
    await ch.handle_update(tap(f"a:{fix.approval_id}:n", uid=51))  # second tap: already decided, nothing happens
    assert "già" in api.answers[-1] and db.get(FixProposal, fix.id).status == "merged"
    assert any("online" in t for t in c.notifier.sent)  # post-merge verification reported back to the phone


async def test_question_binds_decision_and_yes_word_resolves_it(db, seeded, world, settings):
    c, api, ch = await night(db, world, settings)
    await ch.handle_update(msg("Nodo, cosa hai fatto stanotte?"))
    last = api.sent[-1]
    fix = db.scalars(select(FixProposal)).one()
    assert "Dimmi sì o no" in last["text"] and last["buttons"][0][1] == ("Rifiuta", f"a:{fix.approval_id}:n")
    await ch.handle_update(msg("no", uid=2))
    db.expire_all()
    assert db.get(FixProposal, fix.id).status == "rejected" and "scartata" in api.sent[-1]["text"]
    await ch.handle_update(msg("sì", uid=3))
    assert "Non c'è niente" in api.sent[-1]["text"]


async def test_yes_is_ambiguous_with_many_pending(db, seeded, world, settings):
    c, api, ch = await night(db, world, settings)
    db.add(Approval(organization_id=seeded, request_id="x", tool_name="vercel", action="redeploy", level=3,
                    payload={}, status="pending"))
    db.commit()
    await ch.handle_update(msg("sì"))
    assert "pulsanti" in api.sent[0]["text"] and len([m for m in api.sent if m["buttons"]]) == 2
    assert all(a.status == "pending" for a in db.scalars(select(Approval)))


async def test_commands(db, seeded, world, settings):
    c, api, ch = await night(db, world, settings)
    await ch.handle_update(msg("/start"))
    assert "/briefing" in api.sent[-1]["text"]
    await ch.handle_update(msg("/briefing", uid=2))
    assert any("Stanotte" in m["text"] for m in api.sent) and api.sent[-1]["buttons"]
    await ch.handle_update(msg("/siti", uid=3))
    assert "cf-materassi-sito" in api.sent[-1]["text"]
    n = len(api.sent)
    await ch.handle_update(msg("/controlla", uid=4))
    assert "Controllo i siti" in api.sent[n]["text"] and "Ho appena controllato" in api.sent[-1]["text"]
    await ch.handle_update(msg("/approvazioni", uid=5))
    assert "1 decisione" in api.sent[-2]["text"] and api.sent[-1]["buttons"]


async def test_voice_note(db, seeded, world, settings):
    c, api, ch = await night(db, world, settings)
    upd = {"update_id": 9, "message": {"date": int(time.time()), "chat": {"id": int(OWNER)},
                                       "voice": {"file_id": "f1", "mime_type": "audio/ogg"}}}
    c.stt = type("BrowserOnly", (), {"name": "browser", "location": "browser"})()
    await ch.handle_update(upd)
    assert "riconoscimento vocale" in api.sent[-1]["text"]  # browser STT cannot transcribe on the server

    class ServerSTT:
        name, location = "fake-server", "cloud"

        async def transcribe(self, audio, mime, language="it"):
            assert audio == b"OGG" and mime == "audio/ogg"
            return Transcript("come stanno i siti?", "it", None, "fake")
    c.stt, api.files["f1"] = ServerSTT(), b"OGG"
    await ch.handle_update(upd)
    assert "Ho capito: «come stanno i siti?»" in api.sent[-2]["text"] and "Seguo 3 siti" in api.sent[-1]["text"]


async def test_scheduler_briefing_pushes_decisions_with_buttons(db, seeded, world, settings):
    from nodo.night.scheduler import NightScheduler
    c, api, ch = await night(db, world, settings)
    out = await NightScheduler(c).briefing(trigger="test")
    assert out["sent"] and c.notifier.buttons[-1][0][0][0] == "Pubblica"


async def test_http_api_and_notifier_shapes():
    seen = []

    def h(req: httpx.Request):
        body = json.loads(req.content) if req.content else {}
        seen.append((req.url.path.rsplit("/", 1)[-1], body))
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [msg("ciao", uid=5)]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    t = httpx.MockTransport(h)
    api = TelegramApi("123:abc", transport=t)
    ups = await api.get_updates(3, timeout=1)
    assert ups[0]["update_id"] == 5 and seen[0][1]["offset"] == 3 and seen[0][1]["timeout"] == 1
    await api.send_message(OWNER, "x", [[("Pubblica", "a:1:y")]])
    assert seen[-1][1]["reply_markup"] == {"inline_keyboard": [[{"text": "Pubblica", "callback_data": "a:1:y"}]]}
    n = TelegramNotifier("123:abc", OWNER, transport=t)
    assert await n.send("y" * 8000, buttons=[[("Ok", "a:1:y")]])
    sends = [b for m, b in seen if m == "sendMessage"]
    assert len(sends) == 4 and "reply_markup" not in sends[-2] and sends[-1]["reply_markup"]
    assert chunks("a\nb") == ["a\nb"] and len(chunks("x" * 9000)) == 3
