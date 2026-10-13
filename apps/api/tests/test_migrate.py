"""`python -m nodo.db.migrate` must cope with a database whose tables were made by the dev start-up (create_all)."""
from __future__ import annotations

from sqlalchemy import inspect, text

from nodo.db import migrate
from nodo.db.models import Base
from nodo.db.session import make_engine


def _run(monkeypatch, url):
    from nodo.config import get_settings
    monkeypatch.setenv("NODO_DATABASE_URL", url)         # alembic's env.py reads the URL from the settings
    get_settings.cache_clear()
    try:
        assert migrate.main() == 0
    finally:
        get_settings.cache_clear()


def test_database_made_by_create_all_is_stamped_then_upgraded(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'a.db'}"
    eng = make_engine(url)
    Base.metadata.create_all(eng)                       # every table, as the running app does
    _run(monkeypatch, url)
    with eng.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "f4c9a6b3d7e2"
    _run(monkeypatch, url)                              # and a second run is a no-op


def test_database_stuck_before_reminders_still_upgrades(tmp_path, monkeypatch):
    """The state of a PC that already hit the error: stamped at knowledge, reminders table exists."""
    url = f"sqlite:///{tmp_path / 'b.db'}"
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(text("create table alembic_version (version_num varchar(32) not null primary key)"))
        c.execute(text("insert into alembic_version values ('d9a1c4e7b2f3')"))
    _run(monkeypatch, url)
    assert "reminders" in inspect(eng).get_table_names()
