"""FastAPI application factory. Run: `uvicorn nodo.main:app --reload` from apps/api."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nodo.api import board, command, entities, files, greeting, knowledge, ledger, night, pulse, voice, voice_ws
from nodo.api import mind as mind_api
from nodo.api import team as team_api
from nodo.app import build_container
from nodo.config import Settings, get_settings
from nodo.db.session import get_engine, session_scope

log = logging.getLogger("nodo")


def _remove_sample_data() -> None:
    """Your real NODO must not show the fictional sample projects: back up the database, then remove them."""
    from nodo.db.session import backup_sqlite
    from nodo.seed import has_demo, remove_demo
    try:
        with session_scope() as db:
            if not has_demo(db):
                return
        backup = backup_sqlite("prima-pulizia-dati-prova")
        with session_scope() as db:
            out = remove_demo(db)
        log.info("sample data removed %s (backup: %s)", out, backup)
    except Exception:
        log.exception("could not remove the sample data")


def create_app(settings: Settings | None = None, container=None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "httpcore"):   # their INFO lines print full URLs: secret calendar link, Telegram token
        logging.getLogger(noisy).setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from nodo.db.models import Base
        Base.metadata.create_all(get_engine())  # dev convenience; migrations are the source of truth (alembic)
        if settings.seed_demo_data:
            from nodo.seed import seed_demo
            with session_scope() as db:
                seed_demo(db)
        elif settings.remove_demo_data and settings.env not in ("TEST", "DEMO"):
            _remove_sample_data()
        if settings.env not in ("TEST", "DEMO"):
            try:                                   # shop sales live in the register now, not in loose notes
                from nodo.ledger import archive_shop_notes
                with session_scope() as db:
                    if (n := archive_shop_notes(db)):
                        log.info("archived %d old shop notes from memory (the register is the source now)", n)
            except Exception:
                log.exception("could not archive old shop notes")
        c = app.state.container
        from datetime import UTC, datetime
        c.started_at = datetime.now(UTC)
        if settings.env == "DEMO":
            from nodo.night.demo import bootstrap_demo
            await bootstrap_demo(c)
        if settings.night_enabled and settings.env != "TEST":
            from nodo.night.scheduler import NightScheduler
            c.scheduler = NightScheduler(c)
            c.scheduler.start()
        s = settings
        if s.telegram_bot_token and s.telegram_chat_id and s.telegram_commands and s.env not in ("TEST", "DEMO"):
            from nodo.channels.telegram import TelegramApi, TelegramChannel
            c.telegram = TelegramChannel(c, TelegramApi(s.telegram_bot_token), s.telegram_chat_id)
            c.telegram.start()
        log.info("NODO CORE started env=%s mode=%s providers=%s vercel=%s github=%s night=%s", settings.env,
                 settings.mode, [p.name for p in c.providers], type(c.vercel).__name__ if c.vercel else None,
                 type(c.repo).__name__ if c.repo else None, "on" if c.scheduler else "off")
        yield
        if c.scheduler is not None:
            await c.scheduler.stop()
        if c.telegram is not None:
            await c.telegram.stop()

    app = FastAPI(title="NODO CORE", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.container = container or build_container(settings)
    from nodo.launch import fingerprint
    build_id = fingerprint()
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(entities.router)
    app.include_router(command.router)
    app.include_router(voice.router)
    app.include_router(voice_ws.router)
    app.include_router(night.router)
    app.include_router(knowledge.router)
    app.include_router(files.router)
    app.include_router(pulse.router)
    app.include_router(greeting.router)
    app.include_router(ledger.router)
    app.include_router(board.router)
    app.include_router(mind_api.router)
    app.include_router(team_api.router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "nodo-core", "build_id": build_id,
                "mode": settings.mode, "env": settings.env}

    # one process, one URL: serve the built web app (apps/web/dist) when present
    dist = Path(os.environ.get("NODO_WEB_DIST") or Path(__file__).resolve().parents[2] / "web" / "dist")
    if (dist / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        @app.middleware("http")
        async def fresh_panel(request, call_next):
            # the app window (127.0.0.1) kept showing an OLD panel from its cache after an update, while
            # localhost showed the new one: the page itself is always revalidated; its hashed files can be kept
            response = await call_next(request)
            path = request.url.path
            if not path.startswith("/api"):
                response.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                                     if path.startswith("/assets/") else "no-cache")
            return response
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


app = create_app()
