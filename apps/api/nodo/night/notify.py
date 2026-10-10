"""Notifications: how NODO reaches you when you are not looking at the dashboard (morning briefing, critical
alerts). Telegram is the default channel because a bot needs only a token and a chat id, and it lands on your
phone. Without configuration notifications are simply not sent (the briefing is still in the dashboard)."""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger("nodo.notify")


class Notifier(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def send(self, text: str) -> bool: ...


class NullNotifier:
    name = "none"
    enabled = False

    async def send(self, text: str) -> bool:
        return False


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, transport: httpx.AsyncBaseTransport | None = None):
        self.bot_token, self.chat_id, self.transport = bot_token, chat_id, transport

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as c:
                for i in range(0, len(text), 3900):  # Telegram limit is 4096 chars per message
                    r = await c.post(url, json={"chat_id": self.chat_id, "text": text[i:i + 3900],
                                                "disable_web_page_preview": True})
                    if r.status_code >= 400:
                        log.warning("telegram send failed: HTTP %s %s", r.status_code, r.text[:200])
                        return False
            return True
        except httpx.HTTPError as e:
            log.warning("telegram send failed: %s", e)
            return False


class FakeNotifier:
    name = "fake"
    enabled = True

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def build_notifier(settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return NullNotifier()
