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

    async def send(self, text: str, buttons=None) -> bool: ...


class NullNotifier:
    name = "none"
    enabled = False
    supports_buttons = False

    async def send(self, text: str, buttons=None) -> bool:
        return False


class TelegramNotifier:
    name = "telegram"
    supports_buttons = True

    def __init__(self, bot_token: str, chat_id: str, transport: httpx.AsyncBaseTransport | None = None):
        from nodo.channels.telegram import TelegramApi
        self.bot_token, self.chat_id = bot_token, chat_id
        self.api = TelegramApi(bot_token, transport=transport)

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str, buttons=None) -> bool:
        from nodo.channels.telegram import TelegramError, chunks
        try:
            parts = chunks(text)
            for i, part in enumerate(parts):  # buttons go under the last part
                await self.api.send_message(self.chat_id, part, buttons if i == len(parts) - 1 else None)
            return True
        except (TelegramError, httpx.HTTPError) as e:
            log.warning("telegram send failed: %s", e)
            return False


class FakeNotifier:
    name = "fake"
    enabled = True
    supports_buttons = True

    def __init__(self):
        self.sent: list[str] = []
        self.buttons: list[list | None] = []

    async def send(self, text: str, buttons=None) -> bool:
        self.sent.append(text)
        self.buttons.append(buttons)
        return True


def build_notifier(settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return NullNotifier()
