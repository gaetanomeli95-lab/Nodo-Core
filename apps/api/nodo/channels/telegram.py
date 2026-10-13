"""Telegram, both ways: NODO writes to you (briefing, alerts, decisions with buttons) and you write to NODO
(questions, commands, "sì"/"no", voice notes) from your phone.

* Long polling (getUpdates): no public URL, no webhook, works behind any home router.
* Only YOUR chat is served (NODO_TELEGRAM_CHAT_ID); everything else is ignored.
* Buttons are bound to one exact approval (`a:<approval_id>:y|n`): tapping one resolves that row and nothing
  else (ADR-006). A typed "sì" works only for the decision NODO just showed you, or when exactly one is pending.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from nodo.app import Container

log = logging.getLogger("nodo.telegram")
Buttons = list[list[tuple[str, str]]]
MAX_LEN = 3900
_YES = re.compile(r"^\s*(sì|si|yes|ok|okay|va bene|confermo|conferma|procedi|vai|pubblica|approvo|fallo)[\s.!]*$", re.I)
_NO = re.compile(r"^\s*(no|nope|rifiuta|rifiuto|lascia stare|non farlo|annulla|stop)[\s.!]*$", re.I)
PREVIEW_IT = {"ok": "anteprima funzionante", "failed": "ATTENZIONE: anteprima non funzionante",
              "protected": "anteprima protetta: controllala tu", "pending": "anteprima in costruzione",
              "unknown": "anteprima non verificata"}
HELP = ("Sono NODO. Puoi scrivermi (o mandarmi un vocale) come faresti a voce, per esempio:\n"
        "«cosa hai fatto stanotte?» · «come stanno i siti?» · «cosa devo approvare?» · "
        "«sul sito di … cambia il titolo della home» · «segnati di … venerdì»\n\n"
        "Comandi rapidi: /briefing /siti /controlla /approvazioni /aiuto\n"
        "Quando ti propongo una decisione puoi usare i pulsanti oppure rispondere «sì» o «no».")


class TelegramError(RuntimeError):
    pass


def chunks(text: str, size: int = MAX_LEN) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def decision_buttons(approval_id: str, is_fix: bool) -> Buttons:
    return [[("Pubblica" if is_fix else "Approva", f"a:{approval_id}:y"), ("Rifiuta", f"a:{approval_id}:n")]]


def decision_message(p: dict, fix: Any | None = None) -> tuple[str, Buttons]:
    """Text + buttons for one pending decision (from briefing.pending_decisions)."""
    lines = [f"Da decidere: {p['label']}."]
    if fix is not None:
        if fix.diagnosis:
            lines.append(fix.diagnosis)
        meta = [PREVIEW_IT.get(fix.preview_status, fix.preview_status)]
        if fix.files:
            meta.append(", ".join(f"{f['path']} +{f['added']}/-{f['removed']}" for f in fix.files))
        lines.append(" · ".join(meta))
        if fix.preview_url:
            lines.append(f"Anteprima: {fix.preview_url}")
        if fix.pr_url:
            lines.append(f"Pull request: {fix.pr_url}")
    return "\n".join(lines), decision_buttons(p["approval_id"], bool(p.get("fix_id")))


# ---------------------------------------------------------------------------------------------- API client
class TelegramApi:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None, base: str = "https://api.telegram.org"):
        self.token, self.transport, self.base = token, transport, base

    async def call(self, method: str, http_timeout: float = 20, **params) -> Any:
        async with httpx.AsyncClient(timeout=http_timeout, transport=self.transport) as c:
            r = await c.post(f"{self.base}/bot{self.token}/{method}", json={k: v for k, v in params.items() if v is not None})
        data = r.json() if r.content else {}
        if r.status_code >= 400 or not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description') or r.status_code}")
        return data.get("result")

    @staticmethod
    def _markup(buttons: Buttons | None) -> dict | None:
        if not buttons:
            return None
        return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        return await self.call("getUpdates", http_timeout=timeout + 10, offset=offset, timeout=timeout,
                               allowed_updates=["message", "callback_query"]) or []

    async def send_message(self, chat_id: str | int, text: str, buttons: Buttons | None = None) -> dict:
        return await self.call("sendMessage", chat_id=chat_id, text=text[:4096], disable_web_page_preview=True,
                               reply_markup=self._markup(buttons))

    async def edit_message(self, chat_id: str | int, message_id: int, text: str) -> None:
        with contextlib.suppress(TelegramError):
            await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4096])

    async def answer_callback(self, callback_id: str, text: str) -> None:
        with contextlib.suppress(TelegramError):
            await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])

    async def send_chat_action(self, chat_id: str | int, action: str = "typing") -> None:
        with contextlib.suppress(TelegramError):
            await self.call("sendChatAction", chat_id=chat_id, action=action)

    async def download(self, file_id: str) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as c:
            r = await c.get(f"{self.base}/file/bot{self.token}/{info['file_path']}")
        if r.status_code >= 400:   # raise_for_status would print the URL (and the token) into logs
            raise TelegramError(f"download: {r.status_code}")
        return r.content


@dataclass
class FakeTelegramApi:
    """In-memory Telegram for tests: `sent` records outgoing messages, `updates` feeds incoming ones."""
    sent: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        out = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        return out

    async def send_message(self, chat_id, text, buttons=None) -> dict:
        self.sent.append({"chat_id": str(chat_id), "text": text, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def edit_message(self, chat_id, message_id, text) -> None:
        self.edits.append({"message_id": message_id, "text": text})

    async def answer_callback(self, callback_id, text) -> None:
        self.answers.append(text)

    async def send_chat_action(self, chat_id, action="typing") -> None:
        return None

    async def download(self, file_id: str) -> bytes:
        return self.files[file_id]


# ---------------------------------------------------------------------------------------------- channel
class TelegramChannel:
    def __init__(self, container: Container, api, chat_id: str | int, max_age_s: int = 900):
        self.c, self.api, self.chat_id = container, api, str(chat_id)
        self.max_age_s = max_age_s
        self.offset: int | None = None
        self.last_presented: tuple[str, float] | None = None   # (approval_id, monotonic time)
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="nodo-telegram")
            log.info("telegram channel listening (chat %s)", self.chat_id)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        backoff = 2
        while True:
            try:
                await self.poll_once(timeout=25)
                backoff, self.last_error = 2, None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network down, token revoked...: keep trying, slowly
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("telegram poll failed: %s", self.last_error)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    async def poll_once(self, timeout: int = 0) -> int:
        updates = await self.api.get_updates(self.offset, timeout)
        for u in updates:
            self.offset = u["update_id"] + 1
            try:
                await self.handle_update(u)
            except Exception:
                log.exception("telegram update failed")
                with contextlib.suppress(Exception):
                    await self.api.send_message(self.chat_id, "Si è verificato un errore mentre ti rispondevo: "
                                                              "riprova tra poco.")
        return len(updates)

    # ------------------------------------------------------------------ inbound
    def _mine(self, chat: dict | None) -> bool:
        return chat is not None and str(chat.get("id")) == self.chat_id

    async def handle_update(self, u: dict) -> None:
        if cq := u.get("callback_query"):
            if self._mine((cq.get("message") or {}).get("chat")):
                await self.handle_callback(cq)
            return
        msg = u.get("message") or {}
        if not self._mine(msg.get("chat")):
            return  # not our owner: ignore silently (never answer strangers)
        if msg.get("date") and time.time() - msg["date"] > self.max_age_s:
            return  # backlog from while NODO was off: too old to act on
        if voice := (msg.get("voice") or msg.get("audio")):
            await self.handle_voice(voice)
            return
        doc = msg.get("document") or {}
        if msg.get("photo") or (doc.get("mime_type") or "").startswith("image/"):
            await self.handle_photo(msg)
            return
        if text := (msg.get("text") or "").strip():
            await self.handle_text(text)

    async def handle_voice(self, voice: dict) -> None:
        stt = self.c.stt
        if getattr(stt, "location", "browser") == "browser":
            await self.say("Per capire i vocali mi serve un riconoscimento vocale sul server "
                           "(NODO_STT_PROVIDER=openai). Intanto scrivimi pure.")
            return
        audio = await self.api.download(voice["file_id"])
        tr = await stt.transcribe(audio, voice.get("mime_type") or "audio/ogg", self.c.settings.default_language)
        if not tr.text.strip():
            await self.say("Non ho capito il vocale, puoi ripetere?")
            return
        await self.say(f"Ho capito: «{tr.text.strip()}»")
        await self.handle_text(tr.text.strip())

    async def handle_photo(self, msg: dict) -> None:
        """A photo from the phone (receipt, delivery note, label): saved among NODO's files, then read by the brain."""
        from datetime import datetime as _dt

        from nodo.db.session import session_scope
        if self.c.files is None:
            await self.say("Non riesco a salvare le foto in questa istanza.")
            return
        if msg.get("photo"):
            best = max(msg["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
            file_id, ext = best["file_id"], "jpg"
        else:
            file_id = msg["document"]["file_id"]
            ext = {"image/png": "png", "image/webp": "webp"}.get(msg["document"].get("mime_type"), "jpg")
        try:
            data = await self.api.download(file_id)
        except TelegramError:
            await self.say("Non sono riuscita a scaricare la foto, riprovi?")
            return
        name = f"foto-{_dt.now():%Y%m%d-%H%M%S}.{ext}"
        p = self.c.files.save_bytes(name, data)
        caption = (msg.get("caption") or "").strip()
        with session_scope() as db:
            core = self.c.core(db)
            self.c.files.register(db, core.org, p.name, caption[:120] or f"Foto da Telegram {_dt.now():%d/%m %H:%M}",
                                  summary="foto mandata da Telegram")
            db.commit()
        await self.ask_nodo(f"[Ti ho mandato una foto: {p.name}] " + (caption or "Guardala e dimmi cosa c'è."))

    async def handle_text(self, text: str) -> None:
        low = text.lower()
        cmd = low.split()[0].split("@")[0] if low.startswith("/") else None
        if cmd in ("/start", "/aiuto", "/help"):
            await self.say(HELP)
            return
        if cmd == "/approvazioni":
            await self.send_pending(intro=True)
            return
        if cmd == "/briefing":
            await self.send_briefing()
            return
        if _YES.match(text) or _NO.match(text):
            await self.answer_words(approve=bool(_YES.match(text)))
            return
        question = {"/siti": "come stanno i siti?", "/controlla": "controlla i siti"}.get(cmd or "", text)
        if cmd == "/controlla" or "controlla" in low:
            await self.say("Controllo i siti, un attimo…")
        await self.ask_nodo(question)

    async def ask_nodo(self, text: str) -> None:
        from nodo.db.session import session_scope
        await self.api.send_chat_action(self.chat_id)
        bound: list[str] = []
        answer = ""
        with session_scope() as db:
            core = self.c.core(db)
            conv_id = self._conversation(db)
            async for ev in core.handle(text, conv_id, channel="telegram"):
                if ev["type"] == "approval.requested" and ev.get("approval_id"):
                    bound.append(ev["approval_id"])
                elif ev["type"] == "done":
                    answer = ev["text"]
                    self._remember_conversation(db, ev["conversation_id"])
                elif ev["type"] == "error":
                    answer = f"Errore: {ev['message']}"
        if bound:
            # the question ends with "vuoi che proceda?": put the buttons right under it
            *rest, last = chunks(answer)
            for part in rest:
                await self.say(part)
            await self.api.send_message(self.chat_id, last, decision_buttons(bound[0], self._is_fix(bound[0])))
            self.last_presented = (bound[0], time.monotonic())
        else:
            for part in chunks(answer or "Non ho una risposta."):
                await self.say(part)

    async def answer_words(self, approve: bool) -> None:
        from nodo.db.session import session_scope
        from nodo.night.briefing import pending_decisions
        with session_scope() as db:
            from nodo.app import ensure_workspace
            pending = pending_decisions(db, ensure_workspace(db)[0].id)
        ids = {p["approval_id"] for p in pending}
        target = None
        if self.last_presented and self.last_presented[0] in ids and time.monotonic() - self.last_presented[1] < 7200:
            target = self.last_presented[0]
        elif len(pending) == 1:
            target = pending[0]["approval_id"]
        if target is None:
            if not pending:
                await self.say("Non c'è niente in attesa di una tua risposta.")
            else:
                await self.say(f"Ci sono {len(pending)} decisioni in attesa: rispondi con i pulsanti, così so a "
                               "quale ti riferisci.")
                await self.send_pending()
            return
        await self.resolve(target, approve)

    async def handle_callback(self, cq: dict) -> None:
        m = re.fullmatch(r"a:([0-9a-f-]{36}):([yn])", cq.get("data") or "")
        if not m:
            await self.api.answer_callback(cq["id"], "Pulsante non valido.")
            return
        msg = cq.get("message") or {}
        out = await self.resolve(m.group(1), m.group(2) == "y", quiet=True)
        await self.api.answer_callback(cq["id"], out)
        if msg.get("message_id"):
            await self.api.edit_message(self.chat_id, msg["message_id"], f"{msg.get('text', '')}\n\n→ {out}")

    async def resolve(self, approval_id: str, approve: bool, quiet: bool = False) -> str:
        from nodo.db.session import session_scope
        from nodo.night import approvals as A
        with session_scope() as db:
            try:
                res = await A.resolve(db, self.c, approval_id, approve, via="telegram")
                text = res.get("message") or ("Fatto." if approve else "Ok, non lo faccio.")
            except A.ApprovalError:
                text = "Questa decisione era già stata presa."
        if self.last_presented and self.last_presented[0] == approval_id:
            self.last_presented = None
        if not quiet:
            await self.say(text)
        return text

    # ------------------------------------------------------------------ outbound helpers
    async def say(self, text: str, buttons: Buttons | None = None) -> None:
        await self.api.send_message(self.chat_id, text, buttons)

    def _is_fix(self, approval_id: str) -> bool:
        from sqlalchemy import select

        from nodo.db.models import FixProposal
        from nodo.db.session import session_scope
        with session_scope() as db:
            return db.scalars(select(FixProposal).where(FixProposal.approval_id == approval_id)).first() is not None

    async def send_pending(self, intro: bool = False) -> int:
        from nodo.app import ensure_workspace
        from nodo.db.models import FixProposal
        from nodo.db.session import session_scope
        from nodo.night.briefing import pending_decisions
        with session_scope() as db:
            pending = pending_decisions(db, ensure_workspace(db)[0].id)
            msgs = [decision_message(p, db.get(FixProposal, p["fix_id"]) if p.get("fix_id") else None) for p in pending]
        if intro:
            await self.say("Non c'è niente da approvare." if not pending else
                           f"Hai {len(pending)} {'decisione' if len(pending) == 1 else 'decisioni'} in attesa:")
        for text, buttons in msgs:
            await self.say(text, buttons)
        if len(pending) == 1:
            self.last_presented = (pending[0]["approval_id"], time.monotonic())
        return len(pending)

    async def send_briefing(self) -> None:
        from nodo.app import ensure_workspace
        from nodo.db.session import session_scope
        from nodo.night.briefing import latest_or_live
        with session_scope() as db:
            b = latest_or_live(db, ensure_workspace(db)[0].id, self.c.settings)
        for part in chunks(b.text):
            await self.say(part)
        await self.send_pending()

    # ------------------------------------------------------------------ conversation continuity
    def _conversation(self, db) -> str | None:
        from sqlalchemy import select

        from nodo.db.models import Conversation
        conv = db.scalars(select(Conversation).where(Conversation.channel == "telegram",
                                                     Conversation.title == f"telegram:{self.chat_id}")
                          .order_by(Conversation.updated_at.desc()).limit(1)).first()
        return conv.id if conv else None

    def _remember_conversation(self, db, conversation_id: str) -> None:
        from nodo.db.models import Conversation
        if (conv := db.get(Conversation, conversation_id)) and not conv.title:
            conv.title = f"telegram:{self.chat_id}"


async def send_decisions(notifier, db, org: str, site_id: str | None = None) -> int:
    """Push pending decisions with buttons through a notifier that supports them (Telegram)."""
    if not getattr(notifier, "supports_buttons", False) or not notifier.enabled:
        return 0
    from nodo.db.models import FixProposal, NightAction
    from nodo.night.briefing import pending_decisions
    n = 0
    for p in pending_decisions(db, org):
        if site_id is not None:
            fix = db.get(FixProposal, p["fix_id"]) if p.get("fix_id") else None
            act = None
            if fix is None:
                from sqlalchemy import select
                act = db.scalars(select(NightAction).where(NightAction.approval_id == p["approval_id"])).first()
            if (fix.site_id if fix else act.site_id if act else None) != site_id:
                continue
        text, buttons = decision_message(p, db.get(FixProposal, p["fix_id"]) if p.get("fix_id") else None)
        if await notifier.send(text, buttons=buttons):
            n += 1
    return n
