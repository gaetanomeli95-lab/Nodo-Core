"""Direct Gemini API helpers for brain tools: grounded web search and image generation.
Same key and free tier as the voice fallback. When every Gemini model is out of quota the tools
fall back to free no-key services: DuckDuckGo Lite for search, pollinations.ai for images."""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Free-tier quotas are per model: when the day burns out the preferred one, the ladder keeps the tool alive.
_SEARCH_LADDER = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
_IMAGE_LADDER = ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-2.5-flash-image",
                 "gemini-3-pro-image"]
# errors that mean "try the next model" rather than "report the failure"
_RETRYABLE = ("quota", "HTTP 404", "HTTP 429", "HTTP 503", "HTTP 502", "HTTP 500", "timed out")


def _retryable(e: Exception) -> bool:
    return any(k in str(e) for k in _RETRYABLE)


async def _generate(api_key: str, model: str, body: dict, timeout: float = 60.0,
                    transport: httpx.AsyncBaseTransport | None = None) -> dict:
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as c:
        r = await c.post(f"{_BASE}/models/{model}:generateContent", json=body,
                         headers={"x-goog-api-key": api_key})
    if r.status_code >= 400:
        try:
            detail = (r.json().get("error") or {}).get("message") or ""
        except ValueError:
            detail = r.text
        kind = "quota" if r.status_code == 429 else f"HTTP {r.status_code}"
        raise RuntimeError(f"Gemini {model}: {kind} {detail[:200]}".strip())
    return r.json()


def _parts(data: dict) -> list[dict]:
    cands = data.get("candidates") or []
    return ((cands[0].get("content") or {}).get("parts") or []) if cands else []


async def web_search(api_key: str, model: str, query: str, language: str = "it",
                     transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """One grounded search: Gemini decides what to look up on Google and answers with sources.
    No key or every model out of quota -> DuckDuckGo raw results for the brain to summarize."""
    body = {"contents": [{"parts": [{"text": query}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500,
                                 "thinkingConfig": {"thinkingLevel": "minimal"}}}
    data = None
    errors = []
    if api_key:
        for m in [model, *[x for x in _SEARCH_LADDER if x != model]]:
            try:
                data = await _generate(api_key, m, body, transport=transport)
                break
            except RuntimeError as e:
                errors.append(str(e))
                if "thinking" in str(e).lower():          # older models: no thinking knob
                    retry = {**body, "generationConfig": {k: v for k, v in body["generationConfig"].items()
                                                          if k != "thinkingConfig"}}
                    try:
                        data = await _generate(api_key, m, retry, transport=transport)
                        break
                    except RuntimeError as e2:
                        errors.append(str(e2))
                if not _retryable(e):
                    raise
    if data is None:
        ddg = await duckduckgo(query, transport=transport)  # free fallback, no key: the brain synthesizes
        if ddg["sources"]:
            return {"answer": "", "sources": ddg["sources"], "queries": [query],
                    "note": "quota Gemini finita: questi sono i risultati grezzi di DuckDuckGo — riassumili tu"}
        raise RuntimeError("ricerca web non riuscita: " + "; ".join(errors[-2:]))
    text = " ".join(p.get("text", "") for p in _parts(data)).strip()
    sources = []
    grounding = ((data.get("candidates") or [{}])[0].get("groundingMetadata") or {})
    for ch in grounding.get("groundingChunks") or []:
        web = ch.get("web") or {}
        if web.get("uri"):
            sources.append({"title": web.get("title") or web["uri"], "uri": web["uri"]})
    return {"answer": text, "sources": sources[:8],
            "queries": grounding.get("webSearchQueries") or []}


async def generate_image(api_key: str, model: str, prompt: str,
                         transport: httpx.AsyncBaseTransport | None = None) -> tuple[bytes, str]:
    """Text -> (image bytes, engine name): the Gemini ladder first, then keyless pollinations.ai."""
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    data, errors = None, []
    if api_key:
        for m in [model, *[x for x in _IMAGE_LADDER if x != model]]:
            try:
                data = await _generate(api_key, m, body, timeout=120.0, transport=transport)
                used = m
                break
            except RuntimeError as e:
                errors.append(str(e))
                if not _retryable(e):
                    raise
    if data is None:
        try:
            return await pollinations(prompt, transport=transport), "pollinations"   # free fallback, no key
        except Exception as e:
            errors.append(f"pollinations: {e}")
            raise RuntimeError("generazione immagine non riuscita: " + "; ".join(errors[-2:]))
    for p in _parts(data):
        if (p.get("inlineData") or {}).get("data"):
            return base64.b64decode(p["inlineData"]["data"]), used
    raise RuntimeError(f"Gemini {model}: nessuna immagine nella risposta")


async def duckduckgo(query: str, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """DuckDuckGo Lite html scrape — the free, keyless search: returns raw results with snippets."""
    url = "https://lite.duckduckgo.com/lite/"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": "Mozilla/5.0 NODO"}) as c:
        r = await c.post(url, data={"q": query})
    if r.status_code >= 400:
        return {"sources": []}
    html = r.text
    sources = []
    # result anchors carry class='result-link'; attribute order and quote style vary, so scan tags first
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.S):
        attrs = m.group(1)
        if "result-link" not in attrs and "result__a" not in attrs:
            continue
        hm = re.search(r"""href=["']([^"']+)["']""", attrs)
        if not hm:
            continue
        link, title = hm.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if "uddg=" in link:                               # DDG wraps links: extract the real target
            link = unquote(parse_qs(urlparse(link).query).get("uddg", [link])[0])
        if title and link.startswith("http"):
            sources.append({"title": title, "uri": link})
        if len(sources) >= 8:
            break
    return {"sources": sources}


async def pollinations(prompt: str, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """pollinations.ai — free, keyless image generation: GET the URL, get an image back."""
    url = f"https://image.pollinations.ai/prompt/{quote(prompt[:900])}?nologo=true&width=1024&height=1024"
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": "Mozilla/5.0 NODO"}) as c:
        r = await c.get(url)
    if r.status_code >= 400 or len(r.content) < 5000:
        raise RuntimeError(f"pollinations: HTTP {r.status_code}")
    return r.content
