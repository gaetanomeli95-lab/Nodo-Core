"""Event system: in-process bus + append-only persistent log.

Interactive requests also get a per-request async queue so the API can stream execution events (SSE).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nodo.db.models import EventLog


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    organization_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {"type": self.type, "request_id": self.request_id, "at": self.at.isoformat(), **self.payload}


PERSISTED_PREFIXES = ("agent.", "tool.", "approval.", "memory.", "voice.session", "request.", "model.failed")


class EventBus:
    def __init__(self, session: Session | None = None):
        self.session = session
        self._subscribers: list[Callable[[Event], None]] = []
        self._queues: dict[str, asyncio.Queue] = {}
        self.history: list[Event] = []

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subscribers.append(fn)

    def open_stream(self, request_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[request_id] = q
        return q

    def close_stream(self, request_id: str) -> None:
        if q := self._queues.pop(request_id, None):
            q.put_nowait(None)

    def emit(self, type: str, payload: dict | None = None, *, request_id: str | None = None,
             organization_id: str | None = None) -> Event:
        ev = Event(type, payload or {}, request_id, organization_id)
        self.history.append(ev)
        for fn in self._subscribers:
            fn(ev)
        if request_id and (q := self._queues.get(request_id)):
            q.put_nowait(ev)
        if self.session is not None and type.startswith(PERSISTED_PREFIXES):
            self.session.add(EventLog(event_type=type, request_id=request_id, organization_id=organization_id,
                                      payload=ev.payload))
        return ev
