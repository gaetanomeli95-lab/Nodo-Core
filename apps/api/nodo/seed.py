"""Demo/seed data. Fictional fixtures inspired by a small studio's workload; NOTHING here is referenced by
application logic and nothing here describes real infrastructure, credentials or private repositories.
Run: `python -m nodo.seed` (idempotent)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.app import ensure_workspace
from nodo.db.models import (
    Client,
    ContentItem,
    Decision,
    FactStatus,
    Project,
    Repository,
    Sensitivity,
    Task,
    TaskStatus,
)
from nodo.memory.service import MemoryService


def seed_demo(db: Session) -> dict[str, str]:
    org, user = ensure_workspace(db, name="Studio Nodo")
    org.name = "Studio Nodo"
    if db.scalars(select(Project).where(Project.organization_id == org.id).limit(1)).first():
        return {"status": "already seeded"}
    mem = MemoryService(db, org.id)
    now = datetime.now(UTC)

    def client(name, sector, aliases=(), notes=None):
        c = Client(organization_id=org.id, name=name, slug=name.lower().replace(" ", "-"), sector=sector,
                   aliases=list(aliases), notes=notes)
        db.add(c)
        db.flush()
        mem.relate("organization", org.id, "serves", "client", c.id)
        return c

    def project(name, client, kind, priority, desc, aliases=(), sens=Sensitivity.INTERNAL, updated_days_ago=0):
        p = Project(organization_id=org.id, client_id=client.id if client else None, name=name,
                    slug=name.lower().replace(" ", "-"), kind=kind, priority=priority, description=desc,
                    aliases=list(aliases), sensitivity=sens)
        db.add(p)
        db.flush()
        p.updated_at = now - timedelta(days=updated_days_ago)
        if client:
            mem.relate("client", client.id, "has_project", "project", p.id)
        return p

    def task(p, title, status=TaskStatus.TODO, priority=3, due_days=None, blocked=None):
        due = (now + timedelta(days=due_days)).replace(hour=23, minute=59, second=0) if due_days is not None else None
        db.add(Task(organization_id=org.id, project_id=p.id, title=title, status=status, priority=priority,
                    due_date=due, blocked_reason=blocked))

    prosperya = client("Prosperya", "fintech / consulenza", ["prosperia"])
    pbcare = client("PB CARe", "sanità privata", ["pb care", "pbcare", "pb-care"])
    softcomfort = client("SoftComfort", "arredamento / materassi", ["soft comfort"],
                         notes="Due punti vendita. Tono comunicativo caldo, familiare.")
    cf = client("CF Materassi", "retail materassi", ["cf"])

    crm = project("Prosperya CRM", prosperya, "software", 1, "CRM con core finanziario e fiscale per Prosperya.",
                  ["crm prosperya"], Sensitivity.CLIENT_PRIVATE)
    pb_app = project("PB CARe Piattaforma", pbcare, "software", 1, "Piattaforma gestionale PB CARe.", ["pb care app"],
                     Sensitivity.CLIENT_PRIVATE)
    sc_content = project("SoftComfort Social", softcomfort, "content", 2, "Piano contenuti social SoftComfort.",
                         ["softcomfort contenuti"])
    cf_site = project("CF Materassi Sito", cf, "software", 3, "Sito vetrina CF Materassi.", [], updated_days_ago=14)
    nodo = project("Nodo Core", None, "software", 2, "Il sistema operativo intelligente di Studio Nodo.", ["nodo"])

    db.add(Repository(project_id=crm.id, owner="studio-nodo-demo", name="prosperya-crm"))
    db.add(Repository(project_id=pb_app.id, owner="studio-nodo-demo", name="pbcare-platform"))
    db.add(Repository(project_id=nodo.id, owner="gaetanomeli95-lab", name="Nodo-Core", url="https://github.com/gaetanomeli95-lab/Nodo-Core"))

    task(crm, "Chiudere decisione scope MVP", TaskStatus.IN_PROGRESS, 1, due_days=2)
    task(crm, "Test integrazione modulo fiscale", TaskStatus.TODO, 2, due_days=5)
    task(crm, "Documentare API fatturazione", TaskStatus.TODO, 3)
    task(pb_app, "Risolvere errore deploy staging", TaskStatus.BLOCKED, 1, due_days=-1, blocked="Variabili d'ambiente di staging mancanti")
    task(pb_app, "Follow-up tecnico con il cliente", TaskStatus.TODO, 1, due_days=0)
    task(sc_content, "Registrare contenuti in negozio", TaskStatus.TODO, 2, due_days=1)
    task(cf_site, "Aggiornare listino sul sito", TaskStatus.TODO, 3, due_days=-4)
    task(nodo, "Implementare fase 2 (voce streaming)", TaskStatus.TODO, 2)

    db.add(Decision(project_id=crm.id, title="Perimetro MVP: includere il modulo fatturazione?", status="open"))
    db.add(Decision(project_id=crm.id, title="Multi-tenant fin dal primo rilascio?", status="open"))
    db.add(Decision(project_id=crm.id, title="Core finanziario su PostgreSQL con partizionamento annuale", status="decided",
                    rationale="Volumi fiscali crescono per anno; semplifica archiviazione.", decided_at=now - timedelta(days=20)))
    db.add(Decision(project_id=pb_app.id, title="Hosting serverless + database gestito", status="decided", decided_at=now - timedelta(days=40)))

    mem.remember("project", crm.id, "Core finanziario e fiscale completati.", predicate="status.core_finance",
                 layer="semantic", source_type="manual", sensitivity=Sensitivity.CLIENT_PRIVATE)
    mem.remember("project", crm.id, "Il cliente vuole il CRM in produzione entro fine trimestre.", layer="episodic",
                 source_type="conversation", confidence=0.9, sensitivity=Sensitivity.CLIENT_PRIVATE)
    mem.remember("project", pb_app.id, "Deploy staging in errore da ieri.", layer="episodic", source_type="tool", confidence=0.95)
    mem.remember("client", softcomfort.id, "Due punti vendita.", predicate="stores.count", layer="semantic")
    mem.remember("client", softcomfort.id, "Preferisce contenuti umani e poco pubblicitari.", layer="preference",
                 source_type="conversation", confidence=0.85, status=FactStatus.INFERRED)
    mem.remember("client", softcomfort.id, "Formato ricorrente: 'consiglio del giorno' in negozio.", layer="semantic")
    mem.remember("user", user.id, "Preferisce risposte brevi, in italiano.", layer="preference")

    for title, angle, status, days in [
        ("Promo materassi primavera", "commercial", "published", 30),
        ("Come scegliere il cuscino", "educational", "published", 20),
        ("Offerta weekend", "commercial", "published", 10),
        ("Dietro le quinte del negozio", "narrative", "idea", None),
    ]:
        db.add(ContentItem(client_id=softcomfort.id, project_id=sc_content.id, title=title, kind="video", angle=angle,
                           status=status, channel="instagram",
                           published_at=now - timedelta(days=days) if days else None))
    db.flush()
    return {"status": "seeded", "organization_id": org.id, "projects": str(5)}


if __name__ == "__main__":
    from nodo.db.models import Base
    from nodo.db.session import get_engine, session_scope
    Base.metadata.create_all(get_engine())
    with session_scope() as s:
        print(seed_demo(s))
