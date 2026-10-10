"""DEMO mode (NODO_ENV=DEMO): a complete, offline night shift you can click through without any access.

At start-up: demo workspace + three simulated Vercel sites, a healthy baseline check, then "the night happens"
(a bad deploy takes one site down, a build fails on a network error, a dependency alert appears) and the
night shift runs. Open the dashboard and you find the morning briefing, the rollback, the fix PR waiting for
your approval, the rebuilt deploy and a content draft — all simulated, nothing leaves your machine.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from nodo.db.models import Site
from nodo.db.session import session_scope

log = logging.getLogger("nodo.demo")


async def bootstrap_demo(container) -> bool:
    from nodo.night.scheduler import NightScheduler
    from nodo.seed import seed_demo
    world = container.demo
    if world is None:
        return False
    container.night_verify_delay = container.night_retry_delay = container.night_poll_interval = 0
    with session_scope() as db:
        seed_demo(db)
        if db.scalars(select(Site).limit(1)).first():
            return False
        shift = container.night_shift(db)
        await shift.discover()
        for s in db.scalars(select(Site)):
            if s.name == "pbcare-platform":
                s.key_pages = [{"path": "/contatti", "name": "Contatti"}]
        db.flush()
        await shift.run_pass("light", "demo")        # yesterday evening: everything healthy (last known good)
    world.break_things()                              # what happens during the night
    with session_scope() as db:
        await container.night_shift(db).run_pass("night", "demo")
    sched = container.scheduler or NightScheduler(container)
    await sched.briefing(trigger="demo", send=False)
    log.info("demo night ready: open the dashboard to read the morning briefing")
    return True
