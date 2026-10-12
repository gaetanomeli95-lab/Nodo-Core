"""Brain tools for web search, file creation and image generation."""
from httpx import MockTransport

from nodo.services.files import FileStore
from nodo.services.gemini import generate_image, web_search


async def test_web_search_returns_answer_and_sources():
    def handler(request):
        body = request.read().decode()
        assert "google_search" in body
        return __import__("httpx").Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "Palermo è in Sicilia."}]},
                            "groundingMetadata": {
                                "webSearchQueries": ["palermo sicilia"],
                                "groundingChunks": [{"web": {"uri": "https://example.com/p",
                                                             "title": "Fonte"}}]}}]})
    res = await web_search("key", "model", "dove sta Palermo", transport=MockTransport(handler))
    assert "Palermo" in res["answer"]
    assert res["sources"] == [{"title": "Fonte", "uri": "https://example.com/p"}]


async def test_generate_image_returns_png_bytes():
    import base64
    png = base64.b64encode(b"\x89PNGfakedata").decode()

    def handler(request):
        return __import__("httpx").Response(200, json={
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": png}}]}}]})
    data, engine = await generate_image("key", "model", "un gatto", transport=MockTransport(handler))
    assert data == b"\x89PNGfakedata" and engine == "model"


async def test_generate_image_falls_back_to_pollinations_when_quota_is_gone():
    jpeg = b"\xff\xd8\xff" + b"x" * 6000

    def handler(request):
        if "pollinations" in request.url.host:
            return __import__("httpx").Response(200, content=jpeg)
        return __import__("httpx").Response(429, json={"error": {"message": "quota finita"}})

    data, engine = await generate_image("key", "model", "un gatto", transport=MockTransport(handler))
    assert data == jpeg and engine == "pollinations"


async def test_generate_image_pollinations_works_without_any_key():
    png = b"\x89PNG" + b"y" * 6000

    def handler(request):
        assert "pollinations" in request.url.host   # no Gemini call is even attempted
        return __import__("httpx").Response(200, content=png)

    data, engine = await generate_image("", "model", "un gatto", transport=MockTransport(handler))
    assert data == png and engine == "pollinations"


async def test_web_search_falls_back_to_duckduckgo_without_key():
    html = ('<a class="result-link" href="https://example.com/p">Risultato</a>')

    def handler(request):
        assert "duckduckgo" in request.url.host
        return __import__("httpx").Response(200, text=html)

    res = await web_search("", "model", "cosa è successo oggi", transport=MockTransport(handler))
    assert res["answer"] == "" and res["sources"] == [{"title": "Risultato", "uri": "https://example.com/p"}]
    assert "DuckDuckGo" in res["note"]


def test_file_store_safes_names(tmp_path):
    store = FileStore(tmp_path)
    p = store.save_text("../..//etc/passwd", "x")
    assert p.parent == tmp_path.resolve()
    p = store.save_text("report", "contenuto")
    assert p.suffix == ".md"                       # unknown extension defaults to markdown
    p = store.save_text("lista.csv", "a;b")
    assert p.name == "lista.csv" and p.read_text() == "a;b"
    assert store.get("lista.csv") == p
    assert store.get("nope.txt") is None
