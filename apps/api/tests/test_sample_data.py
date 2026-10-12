"""The fictional sample data can be removed from a real installation without touching real data."""
from __future__ import annotations

from sqlalchemy import select

from nodo.db.models import Client, Project, Site, Task
from nodo.seed import has_demo, remove_demo


def test_remove_demo_keeps_real_data(db, seeded):
    real = Project(organization_id=seeded, name="Sito Timilia", slug="timilia", description="pizzeria")
    db.add(real)
    db.flush()
    db.add(Task(organization_id=seeded, project_id=real.id, title="Aggiornare il menu"))
    crm = db.scalars(select(Project).where(Project.name == "Prosperya CRM")).one()
    site = Site(organization_id=seeded, name="prosperya-site", url="https://www.prosperya.it", project_id=crm.id)
    db.add(site)
    db.flush()
    assert has_demo(db)
    out = remove_demo(db)
    assert out["projects"] == 4 and out["clients"] == 4 and out["tasks"] >= 7
    names = sorted(p.name for p in db.scalars(select(Project)))
    assert names == ["Nodo Core", "Sito Timilia"]                  # the real one and the real repo stay
    assert [t.title for t in db.scalars(select(Task))] == ["Aggiornare il menu"]
    assert db.get(Site, site.id).project_id is None                # real site kept, just unlinked
    assert not db.scalars(select(Client)).all()
    assert not has_demo(db) and remove_demo(db) == {"projects": 0, "clients": 0, "tasks": 0}
