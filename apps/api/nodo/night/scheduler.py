"""In-process scheduler for the night shift. Runs inside the API process (works the same on Windows, macOS,
Linux: no cron, no Task Scheduler entries needed beyond starting NODO).

State lives in the database (NightRun rows), so a restart never loses track: if the PC was off at 01:00 and NODO
starts at 03:00, the night run happens at 03:00 (catch-up); the briefing is composed once per day.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from nodo.db.models import NightRun
from nodo.night import keepawake
from nodo.night.briefing import _parse_hhmm, compose_briefing, tzinfo

if TYPE_CHECKING:
    from nodo.app import Container

log = logging.getLogger("nodo.scheduler")


@dataclass
class SchedulerState:
    last_night_start: datetime | None = None     # most recent full night run (started_at)
    last_check_end: datetime | None = None       # most recent finished run of any checking kind
    last_briefing: datetime | None = None
    running: bool = False


def window_bounds(now: datetime, start: str, end: str, tz) -> tuple[datetime, datetime, bool]:
    """(window_start, window_end, inside) for the window containing `now`, or the most recent one."""
    local = now.astimezone(tz)
    s_t, e_t = _parse_hhmm(start), _parse_hhmm(end)
    s = datetime.combine(local.date(), s_t, tz)
    e = datetime.combine(local.date(), e_t, tz)
    if e <= s:              # window crosses midnight (e.g. 23:00 -> 06:00)
        if local < e:
            s -= timedelta(days=1)
        else:
            e += timedelta(days=1)
    elif local < s:         # before tonight's window: the relevant one is yesterday's
        s, e = s - timedelta(days=1), e - timedelta(days=1)
    return s.astimezone(UTC), e.astimezone(UTC), s <= local < e


def decide(now: datetime, st: SchedulerState, settings, tz) -> list[str]:
    """Pure decision function: which jobs are due now? Returns a subset of ["night", "light", "briefing"]."""
    if st.running:
        return []
    jobs: list[str] = []
    w_start, w_end, inside = window_bounds(now, settings.night_start, settings.night_end, tz)
    if inside:
        if st.last_night_start is None or st.last_night_start < w_start:
            jobs.append("night")
        elif st.last_check_end and now - st.last_check_end >= timedelta(minutes=max(5, settings.night_interval_minutes)):
            jobs.append("light")
    elif settings.day_interval_minutes > 0:
        if st.last_check_end is None or now - st.last_check_end >= timedelta(minutes=settings.day_interval_minutes):
            jobs.append("light")
    local = now.astimezone(tz)
    b_time = datetime.combine(local.date(), _parse_hhmm(settings.briefing_time), tz)
    late = local >= b_time + timedelta(hours=8)  # NODO started in the afternoon: no stale "good morning" message
    if local >= b_time and not late and not inside and \
            (st.last_briefing is None or st.last_briefing < b_time.astimezone(UTC)):
        jobs.append("briefing")
    return jobs


class NightScheduler:
    def __init__(self, container: Container, tick_seconds: float = 30.0):
        self.c = container
        self.settings = container.settings
        self.tz = tzinfo(self.settings.timezone)
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.last_tick: datetime | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._recover_stale()
            self._task = asyncio.create_task(self._loop(), name="nodo-night-scheduler")
            log.info("night scheduler started (window %s-%s %s, briefing %s)", self.settings.night_start,
                     self.settings.night_end, self.settings.timezone, self.settings.briefing_time)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        keepawake.release()

    async def _loop(self) -> None:
        await asyncio.sleep(3)  # let the API finish starting
        while True:
            try:
                await self.tick()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never die: log, retry next tick
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    # ------------------------------------------------------------------ state
    def _recover_stale(self) -> None:
        from nodo.db.session import session_scope
        with session_scope() as db:
            # single process: at start-up nothing can still be running, so "running" rows are leftovers
            for r in db.scalars(select(NightRun).where(NightRun.status == "running")):
                r.status, r.error = "failed", "interrotto (NODO è stato chiuso durante il giro)"
                r.finished_at = datetime.now(UTC)

    def state(self, org: str | None = None) -> SchedulerState:
        from nodo.db.session import session_scope

        def aware(d):
            return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)
        with session_scope() as db:
            q = select(NightRun)
            if org:
                q = q.where(NightRun.organization_id == org)
            night = db.scalars(q.where(NightRun.kind == "night").order_by(NightRun.started_at.desc()).limit(1)).first()
            check = db.scalars(q.where(NightRun.kind.in_(("night", "light", "manual")), NightRun.finished_at.is_not(None))
                               .order_by(NightRun.finished_at.desc()).limit(1)).first()
            brief = db.scalars(q.where(NightRun.kind == "briefing").order_by(NightRun.started_at.desc()).limit(1)).first()
            running = db.scalars(q.where(NightRun.status == "running").limit(1)).first() is not None
            return SchedulerState(aware(night.started_at) if night else None,
                                  aware(check.finished_at) if check else None,
                                  aware(brief.started_at) if brief else None, running or self._lock.locked())

    def next_events(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        w_start, w_end, inside = window_bounds(now, self.settings.night_start, self.settings.night_end, self.tz)
        if not inside and w_end <= now:
            w_start, w_end = w_start + timedelta(days=1), w_end + timedelta(days=1)
        local = now.astimezone(self.tz)
        b = datetime.combine(local.date(), _parse_hhmm(self.settings.briefing_time), self.tz)
        if local >= b:
            b += timedelta(days=1)
        return {"night_window": [w_start.astimezone(self.tz).isoformat(), w_end.astimezone(self.tz).isoformat()],
                "in_window": inside, "next_briefing": b.isoformat()}

    # ------------------------------------------------------------------ tick
    async def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        self.last_tick = now
        _, _, inside = window_bounds(now, self.settings.night_start, self.settings.night_end, self.tz)
        if inside and self.settings.keep_awake:
            keepawake.hold()
        elif not inside:
            keepawake.release()
        jobs = decide(now, self.state(), self.settings, self.tz)
        for job in jobs:
            if job == "briefing":
                await self.briefing(trigger="schedule", now=now)
            else:
                await self.run(job, trigger="schedule")
        return jobs

    async def run(self, kind: str, trigger: str = "manual", site_ids: list[str] | None = None) -> str | None:
        from nodo.db.session import session_scope
        if self._lock.locked():
            return None
        async with self._lock:
            with session_scope() as db:
                shift = self.c.night_shift(db)
                run = await shift.run_pass(kind, trigger, site_ids)
                return run.id

    async def briefing(self, trigger: str = "manual", now: datetime | None = None, send: bool = True) -> dict:
        from nodo.app import ensure_workspace
        from nodo.db.session import session_scope
        from nodo.night.briefing import night_window_start
        now = now or datetime.now(UTC)
        with session_scope() as db:
            org = ensure_workspace(db)[0].id
            since = night_window_start(now, self.settings.night_start, self.tz) - timedelta(hours=6)
            b = compose_briefing(db, org, since, now, self.tz)
            run = NightRun(organization_id=org, request_id=f"briefing-{now:%Y%m%d%H%M%S}"[:36], kind="briefing",
                           trigger=trigger, status="done", summary=b.to_dict(), briefing=b.text, started_at=now,
                           finished_at=now)
            db.add(run)
            db.flush()
            sent = False
            if send and self.c.notifier.enabled:
                text = b.text
                if b.pending:
                    text += "\n\nApri NODO per approvare o rifiutare."
                sent = await self.c.notifier.send(text)
                run.summary = b.to_dict() | {"sent_via": self.c.notifier.name if sent else None}
            return {"run_id": run.id, "sent": sent, **b.to_dict()}
