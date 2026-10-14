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


async def test_chatgpt_export_on_the_desktop_is_found_and_learned(container, db, tmp_path, monkeypatch):
    import json
    import zipfile

    from nodo.app import ensure_workspace
    from nodo.knowledge import distill, finder
    org = ensure_workspace(db)[0].id
    desk = tmp_path / "Desktop"
    desk.mkdir()
    conv = [{"title": "Prezzi materassi", "create_time": 1760000000, "mapping": {
        "a": {"message": {"author": {"role": "user"}, "content": {"content_type": "text", "parts": ["Quanto costa un Arianna H20?"]}, "create_time": 1760000000}, "parent": None, "children": ["b"]},
        "b": {"message": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["Dipende dalla misura."]}, "create_time": 1760000010}, "parent": "a", "children": []}}}]
    with zipfile.ZipFile(desk / "abc123-2026-10-12-11-17-50.zip", "w") as z:
        z.writestr("conversations.json", json.dumps(conv))
    with zipfile.ZipFile(desk / "foto.zip", "w") as z:
        z.writestr("img.txt", "x")                                   # not an export: ignored
    started = []
    monkeypatch.setattr(distill, "start", lambda c, sid: started.append(sid))
    assert [p.name for p in finder.find_exports([desk])] == ["abc123-2026-10-12-11-17-50.zip"]
    res = await finder.import_found(container, db, org, [desk])
    assert res["imported"][0]["conversations"] == 1 and started
    again = await finder.import_found(container, db, org, [desk])
    assert again["imported"] == [] and "conosco già" in again["message"]


async def test_the_team_shows_who_did_what(client, container, db):
    from nodo.app import ensure_workspace
    from nodo.brain.tools import Toolbox
    ensure_workspace(db)
    core = container.core(db)
    conv = core._conversation(None, "text")
    box = Toolbox(core, conv, "req-team", "segna una vendita", "text")
    await box.call("ledger_add", {"kind": "vendita", "item": "rete a doghe", "amount": 40, "counterpart": "Rossi"})
    await box.call("board_pin", {"text": "Chiamare Arrisal"})
    db.commit()
    t = client.get("/api/v1/team").json()
    by = {a["id"]: a for a in t["agents"]}
    assert by["contabile"]["today"] == 1 and by["contabile"]["status"] == "al lavoro"
    assert by["contabile"]["last"]["what"] == "ha segnato nel registro"
    assert by["agenda"]["today"] == 1 and "1 note sulla lavagna" in by["agenda"]["facts"]
    assert by["ricercatrice"]["today"] == 0 and by["ricercatrice"]["status"] == "pronta"
    assert t["leader"]["today"] == 2
