from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from nodo.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # short wait for a concurrent writer (threads); long jobs commit before slow network/model waits instead
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # enforce FKs on SQLite like PostgreSQL would
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:  # readers never block the writer (and vice versa)
                dbapi_conn.execute("PRAGMA journal_mode=WAL")
    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = make_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def configure_engine(engine: Engine) -> None:
    """Used by tests / app factory to inject an engine."""
    global _engine, _SessionLocal
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def session_factory() -> sessionmaker:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


def backup_sqlite(label: str) -> str | None:
    """Consistent copy of a file SQLite database next to it (nodo.db.backup-<label>-<date>). None if not SQLite."""
    import sqlite3
    from datetime import datetime
    engine = get_engine()
    path = engine.url.database
    if engine.url.get_backend_name() != "sqlite" or not path or path == ":memory:":
        return None
    dest = f"{path}.backup-{label}-{datetime.now():%Y%m%d-%H%M%S}"
    raw = engine.raw_connection()
    try:
        target = sqlite3.connect(dest)
        with target:
            raw.driver_connection.backup(target)
        target.close()
    finally:
        raw.close()
    return dest
