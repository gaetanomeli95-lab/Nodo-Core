"""Safe schema upgrade: `python -m nodo.db.migrate` (run from apps/api; the start scripts do it for you).

Alembic is the source of truth, but in development the API also calls `create_all` at start-up, so a database
may already contain tables that its alembic_version does not know about. Upgrading such a database would fail
with "table already exists". This helper first aligns the recorded revision with the tables that really exist
(stamp), then upgrades to head. It never drops or rewrites data.
"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from nodo.config import get_settings
from nodo.db.session import make_engine

# (revision, a table that exists once that revision is applied), oldest first
MARKERS = [("51ea0000e744", "projects"), ("b2f3a91c7d01", "voice_turn_metrics"), ("c7d4e2f1a9b0", "sites"),
           ("d9a1c4e7b2f3", "knowledge_sources"), ("e3b8f5a2c6d1", "reminders"),
           ("f4c9a6b3d7e2", "ledger_entries")]


def main() -> int:
    url = get_settings().database_url
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    engine = make_engine(url)
    tables = set(inspect(engine).get_table_names())
    current = None
    if "alembic_version" in tables:
        with engine.connect() as c:
            row = c.execute(text("select version_num from alembic_version")).first()
            current = row[0] if row else None
    present = [rev for rev, table in MARKERS if table in tables]
    if present:
        newest_present = present[-1]
        order = [r for r, _ in MARKERS]
        if current is None or order.index(newest_present) > order.index(current if current in order else order[0]):
            if current != newest_present:
                print(f"[migrate] tabelle già presenti fino a {newest_present}: allineo la versione (stamp)")
                command.stamp(cfg, newest_present)
    command.upgrade(cfg, "head")
    print("[migrate] database aggiornato")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
