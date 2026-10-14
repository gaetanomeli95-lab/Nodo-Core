"""«Mostrami…»: files of his own folders and links, shown in the right viewer."""
from __future__ import annotations

import os
import time

from nodo import media
from tests.test_brain_extra import box  # noqa: F401  (fixture)


def test_find_local_by_kind_name_and_newest(tmp_path):
    (tmp_path / "Video").mkdir()
    (tmp_path / "Immagini").mkdir()
    old = tmp_path / "Video" / "consegna_materasso.mp4"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 1000, time.time() - 1000))
    new = tmp_path / "Video" / "festa.mp4"
    new.write_bytes(b"x")
    (tmp_path / "Immagini" / "letto andromeda.jpg").write_bytes(b"x")
    roots = [tmp_path]
    assert media.find_local("l'ultimo video", roots)[0] == new
    assert media.find_local("il video della consegna del materasso", roots)[0] == old
    assert media.find_local("la foto del letto Andromeda", roots)[0].name == "letto andromeda.jpg"
    assert media.find_local("video del matrimonio di Napoli", roots)[0] is None


def test_shared_file_is_served_only_with_its_token(client, tmp_path):
    f = tmp_path / "foto.png"
    f.write_bytes(b"\x89PNG fake")
    url = media.share(f)
    r = client.get(url)
    assert r.status_code == 200 and r.content == b"\x89PNG fake" and r.headers["content-type"] == "image/png"
    assert client.get("/api/v1/view/nonvalido/foto.png").status_code == 404


def test_links_become_the_right_player():
    yt = media.from_url("guarda https://www.youtube.com/watch?v=dQw4w9WgXcQ grazie")
    assert yt["kind"] == "youtube" and "youtube-nocookie.com/embed/dQw4w9WgXcQ" in yt["url"]
    assert media.from_url("https://esempio.it/video.mp4")["kind"] == "video"
    assert media.from_url("nessun link qui") is None


async def test_every_kind_of_file_opens_in_the_right_viewer(box, client, tmp_path, monkeypatch):  # noqa: F811
    """«Mostrami…» for every file Gaetano may have: the tool finds it, the viewer kind is right, the address
    serves it, the documents can be read on screen, the rest opens with its program, programs never."""
    from nodo.brain.surfaces import surface_for
    from nodo.services import documents
    from nodo.services.files import FileStore
    box.c.files = FileStore(tmp_path / "nodo-files")
    pc = tmp_path / "pc"
    for folder in ("Desktop", "Documenti", "Immagini", "Video", "Musica", "Download"):
        (pc / folder).mkdir(parents=True)
    md = "# Preventivo Rossi\n\nMaterasso Andromeda 160x190: **420 euro**\n\n| voce | euro |\n|---|---|\n| rete | 100 |"
    files = {
        "Documenti/preventivo rossi.pdf": documents.build("pdf", md, "Preventivo Rossi"),
        "Documenti/contratto affitto.docx": documents.build("docx", md, "Contratto"),
        "Documenti/listino prezzi.xlsx": documents.build("xlsx", md, "Listino"),
        "Documenti/presentazione negozio.pptx": documents.build("pptx", md, "Negozio"),
        "Documenti/clienti.csv": b"nome;telefono\nRossi;333\n",
        "Desktop/appunti.txt": b"comprare reti 80x190",
        "Immagini/letto andromeda.jpg": b"\xff\xd8\xff fake jpg",
        "Immagini/logo negozio.png": b"\x89PNG fake",
        "Video/consegna materasso.mp4": b"\x00\x00\x00\x18ftypmp42",
        "Musica/canzone matrimonio.mp3": b"ID3 fake",
        "Download/film vacanze.mkv": b"fake mkv",
        "Download/installa virus.exe": b"MZ",
    }
    for rel, data in files.items():
        (pc / rel).write_bytes(data)
    monkeypatch.setattr("nodo.media.roots", lambda: [pc])
    cases = [("il preventivo di Rossi in pdf", "pdf", None), ("il contratto dell'affitto", "doc", "Materasso"),
             ("il listino prezzi in excel", "sheet", "rete | 100"), ("la presentazione del negozio", "slides", "Materasso"),
             ("il file dei clienti csv", "sheet", "Rossi | 333"), ("gli appunti sul desktop", "text", "reti"),
             ("la foto del letto Andromeda", "image", None), ("il logo del negozio", "image", None),
             ("il video della consegna", "video", None), ("la canzone del matrimonio", "audio", None),
             ("il film delle vacanze", "other", None)]
    for what, kind, inside in cases:
        res = await box.call("show_on_screen", {"what": what})
        assert res.get("kind") == kind, (what, res)
        card = surface_for("show_on_screen", res)
        assert card["view"] == "viewer" and card["rows"][0]["kind"] == kind
        assert client.get(res["url"]).status_code == 200, what
        if inside:
            assert inside in client.get(res["url"] + "/preview").json()["text"], what
    virus = await box.call("show_on_screen", {"what": "installa virus exe"})
    assert "url" not in virus or not virus["url"].endswith(".exe")
    link = await box.call("show_on_screen", {"what": "https://youtu.be/dQw4w9WgXcQ"})
    assert link["kind"] == "youtube"


async def test_everything_maria_creates_can_be_shown(box, client, tmp_path, monkeypatch):  # noqa: F811
    """Create every kind of thing she can make, then «mostramelo»: each one opens in its viewer."""
    from nodo.services.files import FileStore
    box.c.files = FileStore(tmp_path / "files")
    monkeypatch.setattr("nodo.media.roots", lambda: [])

    async def fake_image(key, model, prompt, transport=None):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 4000, "test"
    monkeypatch.setattr("nodo.services.gemini.generate_image", fake_image)
    md = "# Volantino\n\nSconti sui **materassi**\n\n| modello | prezzo |\n|---|---|\n| Andromeda | 420 |"
    made = [await box.call("create_document", {"name": f"volantino {f}", "format": f, "content": md})
            for f in ("pdf", "docx", "xlsx", "pptx")]
    made += [await box.call("create_file", {"name": n, "content": c}) for n, c in (
        ("pagina.html", "<h1>Ciao</h1>"), ("logo.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("note.txt", "promemoria"), ("listino.csv", "a,b\n1,2"))]
    made.append(await box.call("generate_image", {"description": "un tubo cromato"}))
    assert all("url" in m for m in made), made
    for what, kind in (("volantino pdf", "pdf"), ("volantino docx word", "doc"), ("volantino excel", "sheet"),
                       ("volantino pptx presentazione", "slides"), ("pagina.html", "web"), ("logo.svg", "image"),
                       ("note.txt", "text"), ("listino.csv", "sheet"), ("l'ultima immagine", "image")):
        res = await box.call("show_on_screen", {"what": what})
        assert res.get("kind") == kind, (what, res)
        assert client.get(res["url"]).status_code == 200 or res["url"].startswith("/api/v1/files/"), what


def test_software_projects_are_not_his_photos(tmp_path):
    """«Mostrami l'immagine di Nodo Core» opened an icon from NODO's own code folder on the Desktop."""
    proj = tmp_path / "Desktop" / "NODO Core"
    (proj / "apps" / "web" / "public").mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    (proj / "apps" / "web" / "public" / "nodo core logo.svg").write_text("<svg/>")
    (tmp_path / "Desktop" / "nodo core poster.png").write_bytes(b"\x89PNG")
    hit, _ = media.find_local("l'immagine di nodo core", [tmp_path])
    assert hit is not None and hit.name == "nodo core poster.png"


async def test_a_wrong_parameter_name_does_not_lose_the_image(box, tmp_path, monkeypatch):  # noqa: F811
    """The model called generate_image(prompt=…) instead of description=…: the image was never made and Maria
    said «è pronta». Now the usual names are understood; a failure says its real reason."""
    from nodo.services.files import FileStore
    box.c.files = FileStore(tmp_path / "files")

    async def fake_image(key, model, prompt, transport=None):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 4000, "test"
    monkeypatch.setattr("nodo.services.gemini.generate_image", fake_image)
    made = await box.call("generate_image", {"prompt": "un tubo cromato", "style": "realistico"})
    assert made.get("url", "").endswith(".png"), made

    async def broken(key, model, prompt, transport=None):
        raise RuntimeError("quota finita e servizio di riserva irraggiungibile")
    monkeypatch.setattr("nodo.services.gemini.generate_image", broken)
    failed = await box.call("generate_image", {"description": "un tubo"})
    assert "quota finita" in failed["error"]
