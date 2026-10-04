"""FastAPI application factory. Run: `uvicorn nodo.main:app --reload` from apps/api."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nodo.api import command, entities, voice
from nodo.app import build_container
from nodo.config import Settings, get_settings
from nodo.db.session import get_engine, session_scope

log = logging.getLogger("nodo")


def create_app(settings: Settings | None = None, container=None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from nodo.db.models import Base
        Base.metadata.create_all(get_engine())  # dev convenience; migrations are the source of truth (alembic)
        if settings.seed_demo_data:
            from nodo.seed import seed_demo
            with session_scope() as db:
                seed_demo(db)
        log.info("NODO CORE started env=%s mode=%s providers=%s", settings.env, settings.mode,
                 [p.name for p in app.state.container.providers])
        yield

    app = FastAPI(title="NODO CORE", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.container = container or build_container(settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(entities.router)
    app.include_router(command.router)
    app.include_router(voice.router)

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": settings.mode, "env": settings.env}

    return app


app = create_app()
