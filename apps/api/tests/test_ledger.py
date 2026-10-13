"""The shop register: Gaetano's real 12-13 October at CF Materassi, replayed. Sums and recaps by code."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from nodo.greeting import build_greeting
from nodo.ledger import Ledger, norm_size, parse_day, size_label
from tests.test_brain_extra import box  # noqa: F401  (fixture)

TODAY = date(2026, 10, 13)


@pytest.mark.parametrize("said,size", [("160 x 190", "160x190"), ("160 per 190", "160x190"), ("80x90", "80x190"),
                                       ("160x90", "160x190"), ("matrimoniale", "160x190"),
                                       ("alla francese", "140x190"), ("una piazza e mezzo", "120x190"),
                                       ("90x200", "90x200")])
def test_sizes_as_said_aloud(said, size):
    assert norm_size(said) == size


def test_size_names_and_days():
    assert size_label("160x190") == "160x190 (matrimoniale)" and size_label("140x190") == "140x190 (alla francese)"
    assert parse_day("ieri", TODAY) == date(2026, 10, 12) and parse_day("12/10", TODAY) == date(2026, 10, 12)
    assert parse_day("12 ottobre", TODAY) == date(2026, 10, 12) and parse_day(None, TODAY) == TODAY
    assert parse_day("lunedì", TODAY) == date(2026, 10, 12)        # 13/10/2026 is a Tuesday
    with pytest.raises(ValueError):
        parse_day("boh", TODAY)


def _monday(led: Ledger) -> None:
    for row in (dict(kind="vendita", item="materasso Morpheus Memory due strati", size="160x190", unit_price=210,
                     counterpart="Simone Marrocchino"),
                dict(kind="vendita", item="rete a doghe", size="80x190", amount=40, counterpart="Enrico Patti"),
                dict(kind="vendita", item="materasso Andromeda H25 poliuretano", size="140x190", amount=140,
                     counterpart="Alessandro Nocera"),
                dict(kind="spesa", item="palestra", amount=65), dict(kind="spesa", item="stipendio", amount=40,
                                                                     counterpart="Gaetano"),
                dict(kind="spesa", item="pulizie", amount=18, counterpart="Daniela"),
                dict(kind="spesa", item="fornitore", amount=476, counterpart="Arrisal")):
        led.add(day="ieri", **row)


def test_a_real_day_adds_up_and_the_recap_misses_nothing(db, seeded):
    led = Ledger(db, seeded, "CF Materassi", today=TODAY)
    _monday(led)
    rep = led.report(date(2026, 10, 12), date(2026, 10, 12))
    assert rep["totals"] == {"incassato": 390.0, "da_incassare_sospeso": 0.0, "uscite": 599.0,
                             "saldo_del_periodo": -209.0}
    text = rep["text"]
    for must in ("Morpheus Memory due strati 160x190 (matrimoniale) a 210 euro a Simone Marrocchino",
                 "rete a doghe 80x190 (singolo) a 40 euro a Enrico Patti", "Andromeda H25 poliuretano 140x190",
                 "palestra 65 euro", "fornitore 476 euro ad Arrisal", "incassato 390 euro", "uscite 599 euro"):
        assert must in text, must


def test_today_with_corrections_suspended_payment_and_short_names(db, seeded):
    led = Ledger(db, seeded, "CF Materassi", today=TODAY)
    _monday(led)
    led.add(kind="sostituzione", item="materasso Andromeda", counterpart="signora Silvana",
            note="affossamento, via Luigi Rizzo 55, Aspra")
    sc = led.add(kind="vendita", item="materasso Arianna H20 poliuretano", size="80x90", qty=4, unit_price=110,
                 counterpart="Softcomfort", status="da_fare")
    led.add(kind="vendita", item="rete con tavola", size="matrimoniale", amount=100, counterpart="Softcomfort",
            status="da_fare")
    nocera = led.add(kind="vendita", item="Andromeda H25", size="160x190", amount=150,
                     counterpart="Alessandro Nocera", status="da_fare")
    assert nocera.item == "materasso Andromeda H25 poliuretano"     # completed from the name already used
    # «sono tre, matrimoniali», «costa 140», delivered, Softcomfort has not paid
    assert led.update(sc, qty=3, size="160 x 90", status="fatto", payment="sospeso")
    assert sc.amount == 330.0 and sc.size == "160x190"
    e, _ = led.find("Nocera")
    assert e is nocera                                             # today's one, not yesterday's
    led.update(e, amount=140, status="fatto")
    rete, _ = led.find("rete con tavola Softcomfort")
    led.update(rete, status="fatto", payment="sospeso")
    rep = led.report(TODAY, TODAY)
    assert rep["totals"]["incassato"] == 140.0 and rep["totals"]["da_incassare_sospeso"] == 430.0
    assert "3 × materasso Arianna H20 poliuretano 160x190 (matrimoniale) a 110 euro l'uno, totale 330 euro a " \
           "Softcomfort, pagamento SOSPESO" in rep["text"]
    assert "in garanzia, nessun incasso" in rep["text"]
    goods = {(g["item"], g["size"]): g["pieces"] for g in rep["goods_out"]}
    assert goods[("materasso Arianna H20 poliuretano", "160x190 (matrimoniale)")] == 3
    # two days together, then Softcomfort pays on Thursday
    both = led.report(date(2026, 10, 12), TODAY)
    assert both["totals"]["incassato"] == 530.0 and both["totals"]["uscite"] == 599.0
    thursday = Ledger(db, seeded, "CF Materassi", today=date(2026, 10, 15))
    for r in thursday.open_payments():
        thursday.update(r, payment="pagato")
    th = thursday.report(date(2026, 10, 15), date(2026, 10, 15))
    assert th["totals"]["incassato"] == 430.0 and "Incassi di vendite precedenti" in th["text"]
    assert Ledger(db, seeded, "CF Materassi", today=TODAY).report(TODAY, TODAY)["totals"]["incassato"] == 140.0


async def test_brain_tools_add_correct_and_report(box, db):  # noqa: F811
    out = await box.call("ledger_add", {"entries": [
        {"kind": "vendita", "item": "materasso Morpheus Memory due strati", "size": "160x190", "unit_price": 210,
         "counterpart": "Simone Marrocchino", "day": "ieri"},
        {"kind": "spesa", "item": "palestra", "amount": 65, "day": "ieri"},
        {"kind": "consegna", "item": "materasso Arianna H20", "size": "160 per 190", "qty": 3, "unit_price": 110,
         "counterpart": "Softcomfort", "status": "da fare"},
        {"kind": "boh"}]})
    assert out["saved"] == 3 and out["errors"]
    upd = await box.call("ledger_update", {"entry": "Softcomfort", "status": "fatto", "payment": "sospeso"})
    assert upd["updated"] and any("sospeso" in c for c in upd["changes"])
    assert not (await box.call("ledger_update", {"entry": "nessuno così"}))["updated"]
    rep = await box.call("ledger_report", {"from_day": "ieri", "to_day": "oggi"})
    assert rep["totals"]["incassato"] == 210.0 and rep["totals"]["uscite"] == 65.0
    assert "Morpheus Memory due strati 160x190" in rep["text"]
    opened = await box.call("ledger_open", {})
    assert opened["totale_sospeso"] == 330.0 and opened["pagamenti_sospesi"]
    now = await box.call("now", {})
    assert "ore" in now["now"]


def test_greeting_starts_with_the_shop(db, seeded):
    from nodo.config import Settings
    led = Ledger(db, seeded, "CF Materassi", today=TODAY)
    _monday(led)
    led.add(kind="vendita", item="rete con tavola", size="160x190", amount=100, counterpart="Softcomfort",
            status="da_fare")
    led.add(kind="vendita", item="materasso Arianna", amount=330, counterpart="Softcomfort", payment="sospeso",
            day="ieri")
    db.flush()
    g = build_greeting(db, seeded, Settings(), now=datetime(2026, 10, 13, 7, 30, tzinfo=UTC))
    text = g["text"]
    assert text.index("CF Materassi") < text.index("Da dove")
    assert "incassati 390 euro" in text and "uscite 599 euro" in text and "sospesi 330 euro" in text
    assert "Oggi da fare: rete con tavola 160x190" in text and "Aspetti ancora 330 euro da Softcomfort" in text


def test_panel_api_shows_the_day_and_open_items(client):
    from nodo.app import ensure_workspace
    from nodo.db.session import session_scope
    with session_scope() as s:
        org = ensure_workspace(s)[0].id
        led = Ledger(s, org, "CF Materassi")
        led.add(kind="vendita", item="rete con tavola", size="160x190", amount=100, counterpart="Softcomfort",
                payment="sospeso")
        led.add(kind="spesa", item="palestra", amount=65)
        s.commit()
    day = client.get("/api/v1/ledger").json()
    assert day["totals"]["uscite"] == 65 and day["totals"]["da_incassare_sospeso"] == 100
    assert any("rete con tavola 160x190 (matrimoniale)" in e["sentence"] for e in day["entries"])
    opened = client.get("/api/v1/ledger/open").json()
    assert opened["totale_sospeso"] == 100 and opened["business"] == "CF Materassi"
    assert client.get("/api/v1/ledger", params={"start": "boh"}).status_code == 400
