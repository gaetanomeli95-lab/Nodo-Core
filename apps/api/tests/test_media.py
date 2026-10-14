"""«Mostrami…»: files of his own folders and links, shown in the right viewer."""
from __future__ import annotations

import os
import time

from nodo import media


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
