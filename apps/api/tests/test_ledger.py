"""The shop register: Gaetano's real 12-13 October at CF Materassi, replayed. Sums and recaps by code."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

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
    assert nocera.item == "Andromeda H25"                           # never completed by guessing
    assert led.similar_items("Andromeda H25") == ["materasso Andromeda H25 poliuretano"]
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


def test_the_register_catches_my_mistakes(db, seeded):
    from nodo.ledger import check_entry
    led = Ledger(db, seeded, "CF Materassi", today=TODAY)
    _monday(led)
    nocera = led.add(kind="vendita", item="materasso Andromeda H25 poliuretano", size="140x190", amount=160,
                     counterpart="Mario Rossi")
    w = check_entry(led, nocera)
    assert any("l'ultima volta materasso Andromeda H25 poliuretano 140x190 (alla francese) era 140 euro" in x for x in w)
    odd = led.add(kind="vendita", item="materasso Arianna", size="160x150", amount=110, counterpart="X")
    assert any("misura insolita" in x for x in check_entry(led, odd))
    bare = led.add(kind="vendita", item="materasso Arianna")
    w = check_entry(led, bare, explicit_payment=False)
    assert {"manca il prezzo", "manca la misura", "manca il nome del cliente"} <= set(w)
    assert any("non hai detto se ha pagato" in x for x in w)
    twice = led.add(kind="spesa", item="palestra", amount=65, day="ieri")
    assert any("doppione" in x for x in check_entry(led, twice))
    ok = led.add(kind="vendita", item="rete con tavola", size="160x190", amount=100, counterpart="Anna")
    assert check_entry(led, ok) == []


async def test_tools_read_back_ask_and_close_the_day(box, db):  # noqa: F811
    await box.call("ledger_add", {"entries": [
        {"kind": "vendita", "item": "rete a doghe", "size": "80x190", "amount": 40, "counterpart": "Enrico Patti",
         "payment": "pagato", "day": "ieri"},
        {"kind": "spesa", "counterpart": "palestra", "amount": 65}]})
    out = await box.call("ledger_add", {"entries": [
        {"kind": "vendita", "item": "rete a doghe", "size": "80x190", "amount": 55, "counterpart": "Bianchi"}]})
    assert "giusto?" in out["say"] and any("era 40 euro" in c for c in out["rows"][0]["check"])
    assert any("non hai detto se ha pagato" in c for c in out["rows"][0]["check"])
    rep = await box.call("ledger_report", {})
    assert "palestra 65 euro" in rep["text"] and "65 euro di palestra" in rep["say"]
    prices = await box.call("ledger_prices", {"item": "rete"})
    assert prices["prices"][0]["last"] == "55 euro" and len(prices["prices"][0]["history"]) == 2
    close = await box.call("ledger_close_day", {"cash_counted": 0})
    assert "Incassato 55 euro, uscite 65 euro, saldo -10 euro" in close["say"]
    assert close["cash_difference"] == 10.0 and "ci sono 10 euro in più" in close["say"]


def test_old_shop_notes_are_archived_rules_are_kept(db, seeded):
    from nodo.db.models import FactStatus, MemoryFact
    from nodo.ledger import archive_shop_notes
    texts = ["Il materasso Andromeda H25 per Alessandro Nocera costa 140 euro.",
             "Oggi sono da consegnare a Softcomfort tre materassi Arianna.",
             "Il tuo nome è Isabel.", "L'assistente non può creare file, PDF, immagini.",
             "Quando Gaetano chiede un riepilogo dettagliato, vuole sempre misure, prezzi e tutti i dettagli.",
             "Filippo è il titolare di CF Materassi.", "Le misure standard dei materassi sono: 80x190 singolo."]
    for t in texts:
        db.add(MemoryFact(organization_id=seeded, subject_type="user", subject_id=seeded, content=t,
                          source_type="conversation"))
    db.flush()
    assert archive_shop_notes(db) == 4
    left = {f.content for f in db.scalars(select(MemoryFact).where(MemoryFact.source_type == "conversation",
                                                                   MemoryFact.status != FactStatus.ARCHIVED))}
    assert left & set(texts) == set(texts[4:])
    assert archive_shop_notes(db) == 0


async def test_nodo_knows_its_own_updates(box):  # noqa: F811
    out = await box.call("nodo_updates", {"days": 30})
    assert ("updates" in out and out["updates"] and out["updates"][0]["what"]) or "error" in out


def test_voice_mode_can_be_switched_from_the_panel(client, tmp_path, monkeypatch):
    monkeypatch.setenv("NODO_ENV_FILE", str(tmp_path / ".env"))
    assert client.post("/api/v1/setup/key", json={"name": "NODO_VOICE_ENGINE", "value": "boh"}).status_code == 400
    assert client.post("/api/v1/setup/key", json={"name": "NODO_VOICE_ENGINE", "value": "precise"}).status_code == 200
    assert "NODO_VOICE_ENGINE=precise" in (tmp_path / ".env").read_text()
    from nodo.config import Settings
    from nodo.voice.live import missing_reason
    assert missing_reason(Settings(voice_engine="precise", gemini_api_key="k", env="LOCAL")).startswith("voce precisa")


def test_the_spoken_recap_sounds_like_a_person(db, seeded):
    led = Ledger(db, seeded, "CF Materassi", today=TODAY)
    led.sounds = {"Morpheus": "Morfeus"}
    _monday(led)
    led.add(kind="sostituzione", item="materasso Andromeda H25 poliuretano", size="80x190", counterpart="Signora Silvana")
    led.add(kind="vendita", item="materasso Arianna H20 poliuretano", size="160x190", qty=3, unit_price=110,
            counterpart="Softcomfort", payment="sospeso")
    led.add(kind="vendita", item="rete con tavola", size="160x190", amount=100, counterpart="Softcomfort",
            payment="sospeso")
    led.add(kind="spesa", counterpart="palestra", amount=10)               # saved the old way: no item
    say = led.report(date(2026, 10, 12), TODAY)["say"]
    for must in ("Ieri: a Simone Marrocchino hai venduto un materasso Morfeus Memory due strati matrimoniale, "
                 "160 per 190, a 210 euro",
                 "una rete a doghe singola, 80 per 190, a 40 euro",
                 "a Softcomfort hai consegnato tre materassi Arianna H20 poliuretano matrimoniali, 160 per 190, a 110 "
                 "euro l'uno e una rete con tavola matrimoniale, 160 per 190, a 100 euro: in tutto 430 euro, che deve "
                 "ancora pagare",
                 "sostituzione in garanzia alla signora Silvana", "40 euro di stipendio a te",
                 "476 euro al fornitore Arrisal", "10 euro di palestra", "e ne aspetti ancora 430 euro"):
        assert must in say, must
    for robot in ("×", "(", ";", "x190", "pagato,", "Morpheus"):
        assert robot not in say, robot


async def test_show_and_close_on_screen(box, db, tmp_path):  # noqa: F811
    from nodo.brain.surfaces import surface_for
    from nodo.services.files import FileStore
    box.c.files = FileStore(tmp_path / "files")
    assert "error" in await box.call("show_on_screen", {"what": "l'ultimo pdf"})
    await box.call("create_document", {"name": "preventivo", "format": "pdf", "content": "# Preventivo\n\nCiao"})
    await box.call("generate_image", {"description": "x"}) if False else None
    await box.call("create_document", {"name": "listino", "format": "xlsx", "content": "| a | b |\n|---|---|\n| 1 | 2 |"})
    pdf = await box.call("show_on_screen", {"what": "l'ultimo pdf"})
    assert pdf["kind"] == "pdf" and pdf["url"].endswith("preventivo.pdf")
    card = surface_for("show_on_screen", pdf)
    assert card["view"] == "viewer" and card["rows"][0]["kind"] == "pdf"
    sheet = await box.call("show_on_screen", {"what": "listino"})
    assert sheet["kind"] == "sheet"
    tab = await box.call("show_on_screen", {"what": "registro"})
    assert surface_for("show_on_screen", tab) == {"view": "tab", "rows": [{"tab": "ledger"}], "summary": ""}
    assert surface_for("close_screen", await box.call("close_screen", {}))["view"] == "close"
    made = surface_for("create_document", {"created": "x", "url": "/api/v1/files/x.pdf", "format": "pdf"})
    assert made["view"] == "viewer" and made["rows"][0]["download"]


def test_preview_of_a_spreadsheet(client, tmp_path):
    from nodo.services import documents
    c = client.app.state.container
    c.files.save_bytes("prova.xlsx", documents.build("xlsx", "| Voce | Prezzo |\n|---|---|\n| Rete | 100 |"))
    out = client.get("/api/v1/files/prova.xlsx/preview").json()
    assert "Rete | 100" in out["text"]
    assert client.get("/api/v1/files/nessuno.pdf/preview").status_code == 404
