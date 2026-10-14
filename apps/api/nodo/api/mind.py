"""Maria's mind as a graph («la mente»): what she knows, drawn live in the panel.

Nodes are the real memory: people, rules, preferences, facts, diary days, the shop's customers and products, the
board. Edges are the real links between them (a rule that names a person, a customer who bought a product…).
The panel lights a node up when its name is heard or said, so Gaetano SEES Maria remembering.
"""
from __future__ import annotations

import re
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, get_db, org_id
from nodo.db.models import BoardNote, Conversation, LedgerEntry
from nodo.memory import mind

router = APIRouter(prefix="/api/v1/mind", dependencies=[Depends(auth)])

_DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
_MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
_STOP = {"il", "lo", "la", "i", "gli", "le", "un", "una", "di", "da", "del", "della", "e", "a", "in", "per", "con",
         "signora", "signor", "signore", "sig", "materasso", "rete", "the"}


def _short(text: str, n: int = 30) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _words(label: str) -> list[str]:
    """What, if heard or said, lights the node up: the full name and its distinctive words (≥4 letters)."""
    low = label.lower().strip()
    ws = [w for w in re.findall(r"[a-zà-ù']+", low) if len(w) >= 4 and w not in _STOP]
    return list(dict.fromkeys([low, *ws]))[:5]


def build_graph(db: Session, org: str, owner: str = "Gaetano", assistant: str = "Maria",
                business: str = "CF Materassi", max_nodes: int = 420) -> dict:
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str]] = set()

    def node(nid: str, label: str, kind: str, weight: float = 1.0, words: list[str] | None = None) -> str:
        if nid not in nodes and len(nodes) < max_nodes:
            nodes[nid] = {"id": nid, "label": label, "kind": kind, "weight": weight,
                          "words": words if words is not None else _words(label)}
        elif nid in nodes:
            nodes[nid]["weight"] += weight * 0.25
        return nid

    def link(a: str, b: str) -> None:
        if a in nodes and b in nodes and a != b:
            edges.add((a, b) if a < b else (b, a))

    core = node("maria", assistant, "core", 6, [assistant.lower()])
    me = node("gaetano", owner, "hub", 4, [owner.lower()])
    hubs = {key: node(f"hub:{key}", label, "hub", 3, words)
            for key, label, words in (("negozio", business, [business.lower(), "negozio", "registro"]),
                                      ("persone", "Persone", ["persone"]), ("regole", "Regole", ["regola", "regole"]),
                                      ("ricordi", "Ricordi", ["ricordi", "ricorda", "memoria"]),
                                      ("clienti", "Clienti", ["clienti", "cliente"]),
                                      ("prodotti", "Prodotti", ["prodotti", "listino"]),
                                      ("diario", "Diario", ["diario", "ieri", "oggi"]),
                                      ("lavagna", "Lavagna", ["lavagna"]))}
    link(core, me)
    for h in hubs.values():
        link(core, h)
    link(me, hubs["negozio"])
    link(hubs["negozio"], hubs["clienti"])
    link(hubs["negozio"], hubs["prodotti"])

    # people Maria knows (one node per person, never merged)
    people = mind.people(db, org)
    person_ids: dict[str, str] = {}
    for slug, facts in people.items():
        label = next((f.predicate for f in facts if f.predicate), None) or slug.replace("-", " ").title()
        pid = node(f"p:{slug}", label, "person", 1 + 0.4 * len(facts))
        person_ids[label.lower()] = pid
        link(pid, hubs["persone"])

    def mentions(text: str) -> list[str]:
        low = (text or "").lower()
        return [pid for name, pid in person_ids.items()
                if name in low or any(w in low for w in nodes[pid]["words"][1:2])]

    for kind, hub, nkind, cap in (("rule", "regole", "rule", 60), ("prefer", "gaetano", "pref", 40),
                                  ("fact", "ricordi", "fact", 140)):
        facts = mind.active(db, org, kind)[-cap:]
        for f in facts:
            fid = node(f"f:{f.id}", _short(f.content), nkind, 1, _words(_short(f.content, 60))[1:4])
            link(fid, me if hub == "gaetano" else hubs[hub])
            for pid in mentions(f.content):
                link(fid, pid)

    for f in mind.active(db, org, "episode")[-14:]:
        try:
            from datetime import date
            d = date.fromisoformat(f.subject_id)
            label = f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"
        except ValueError:
            label = _short(f.subject_id, 14)
        did = node(f"d:{f.subject_id}", label, "day", 1, [])
        link(did, hubs["diario"])
        for pid in mentions(f.content):
            link(did, pid)

    # the shop: who buys what (the register is memory too)
    rows = db.execute(select(LedgerEntry.counterpart, LedgerEntry.item, LedgerEntry.kind)
                      .where(LedgerEntry.organization_id == org, LedgerEntry.status != "annullato")
                      .order_by(LedgerEntry.at.desc()).limit(1500)).all()
    who = Counter(c.strip() for c, _, k in rows if c and k in ("vendita", "credito", "sostituzione"))
    what = Counter(" ".join(i.split()[:3]).strip() for _, i, k in rows if i and k in ("vendita", "sostituzione"))
    for name, n in who.most_common(45):
        cid = person_ids.get(name.lower()) or node(f"c:{name.lower()}", name, "customer", 1 + min(n, 20) * 0.15)
        link(cid, hubs["clienti"])
    for item, n in what.most_common(24):
        node(f"i:{item.lower()}", item, "product", 1 + min(n, 30) * 0.12)
        link(f"i:{item.lower()}", hubs["prodotti"])
    pairs = Counter((c.strip().lower(), " ".join(i.split()[:3]).strip().lower()) for c, i, k in rows
                    if c and i and k in ("vendita", "sostituzione"))
    for (c, i), _n in pairs.most_common(160):
        cid = person_ids.get(c) or f"c:{c}"
        link(cid, f"i:{i}")

    for n in db.scalars(select(BoardNote).where(BoardNote.organization_id == org, BoardNote.done_at.is_(None))
                        .order_by(BoardNote.created_at.desc()).limit(12)):
        nid = node(f"b:{n.id}", _short(n.text, 26), "note", 1, _words(_short(n.text, 50))[1:3])
        link(nid, hubs["lavagna"])
        for pid in mentions(n.text):
            link(nid, pid)

    # the old chats with ChatGPT/Claude: one node per conversation, around «Vecchie chat»
    from nodo.db.models import Document, KnowledgeChunk
    docs = db.execute(select(Document.id, Document.title)
                      .where(Document.id.in_(select(KnowledgeChunk.document_id)
                                             .where(KnowledgeChunk.organization_id == org).distinct()))
                      .order_by(Document.created_at.desc()).limit(140)).all()
    if docs:
        old_chats = node("hub:chat", "Vecchie chat", "hub", 3, ["chatgpt", "vecchie chat", "conversazioni"])
        link(core, old_chats)
        for did, title in docs:
            if title:
                tid = node(f"k:{did}", _short(title, 28), "topic", 1, _words(_short(title, 60))[1:4])
                link(tid, old_chats)
                for pid in mentions(title):
                    link(tid, pid)

    from nodo.knowledge.service import KnowledgeService
    k = KnowledgeService(db, org).overview()
    stats = {"nodi": len(nodes), "collegamenti": len(edges),
             "ricordi": sum(1 for x in nodes.values() if x["kind"] in ("rule", "pref", "fact", "person", "day")),
             "clienti": len(who),
             "conversazioni": int(k.get("conversations") or 0)
             + int(db.scalar(select(func.count()).select_from(Conversation)) or 0)}
    return {"nodes": list(nodes.values()), "edges": [list(e) for e in sorted(edges)], "stats": stats}


@router.get("/graph")
def graph(db: Session = Depends(get_db), org: str = Depends(org_id), c=Depends(container)):
    s = c.settings
    return build_graph(db, org, s.owner_name, s.assistant_name, s.ledger_business)
