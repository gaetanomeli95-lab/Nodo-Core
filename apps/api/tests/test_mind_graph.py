"""The mind graph: Maria's real memory drawn as nodes and links."""
from __future__ import annotations

from datetime import date


def test_mind_graph_shows_people_rules_customers_and_links(client, db):
    from nodo.app import ensure_workspace
    from nodo.ledger import Ledger
    from nodo.memory import mind
    org = ensure_workspace(db)[0].id
    mind.apply_ops(db, org, org, [
        {"op": "add", "kind": "person", "name": "Marcello Lo Monaco", "text": "amico di Gaetano, ci deve 30 euro"},
        {"op": "add", "kind": "person", "name": "Ciccio Lotà", "text": "Ciccio Lotà si scrive con la T"},
        {"op": "add", "kind": "rule", "text": "Quando arriva Marcello Lo Monaco di': catu i munnizza"},
    ])
    led = Ledger(db, org, "CF Materassi", today=date(2026, 10, 14))
    led.add(kind="vendita", item="materasso Arianna H20 poliuretano", amount=110, counterpart="Softcomfort")
    led.add(kind="credito", item="prestito", amount=30, counterpart="Marcello Lo Monaco")
    db.commit()
    g = client.get("/api/v1/mind/graph").json()
    by = {n["id"]: n for n in g["nodes"]}
    assert by["maria"]["kind"] == "core" and "hub:negozio" in by
    assert by["p:marcello-lo-monaco"]["kind"] == "person" and "p:ciccio-lota" in by          # never merged
    rule = next(n for n in g["nodes"] if n["kind"] == "rule")
    edges = {tuple(e) for e in g["edges"]}
    assert tuple(sorted((rule["id"], "p:marcello-lo-monaco"))) in edges                     # the rule names him
    assert "c:softcomfort" in by and tuple(sorted(("c:softcomfort", "i:materasso arianna h20"))) in edges
    assert "marcello" in by["p:marcello-lo-monaco"]["words"]
    assert g["stats"]["nodi"] == len(g["nodes"]) and g["stats"]["collegamenti"] == len(g["edges"])
