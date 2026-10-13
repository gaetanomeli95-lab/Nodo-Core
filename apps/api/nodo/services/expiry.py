"""When do things run out? HTTPS certificate of a site and registration of its domain (public RDAP, no key)."""
from __future__ import annotations

import asyncio
import ssl
import time

_SHARED_HOSTS = ("vercel.app", "netlify.app", "github.io", "pages.dev", "onrender.com", "herokuapp.com")


async def certificate_days(host: str, port: int = 443, timeout: float = 8.0) -> tuple[int | None, str]:
    """(days left, "") - or (None, reason) when the certificate cannot be read or is not valid (which is news too)."""
    ctx = ssl.create_default_context()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout)
    except ssl.SSLCertVerificationError as e:
        return None, f"certificato non valido: {e.verify_message}"
    except (TimeoutError, OSError, ssl.SSLError) as e:
        return None, f"non riesco a collegarmi ({type(e).__name__})"
    try:
        cert = writer.get_extra_info("peercert") or {}
    finally:
        writer.close()
    try:
        left = (ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) / 86400
    except (KeyError, ValueError):
        return None, "scadenza non leggibile"
    return int(left), ""


async def domain_expiry(host: str, client=None) -> str | None:
    """Registration expiry date (YYYY-MM-DD) from RDAP, or None when unknown / not an owned domain."""
    import httpx
    host = host.lower().strip(".")
    if host.endswith(_SHARED_HOSTS):
        return None
    labels = host.split(".")
    tries = [".".join(labels[-n:]) for n in (2, 3) if len(labels) >= n]
    own = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=True)
    try:
        for dom in tries:
            try:
                r = await client.get(f"https://rdap.org/domain/{dom}")
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            for ev in r.json().get("events", []):
                if ev.get("eventAction") == "expiration" and ev.get("eventDate"):
                    return ev["eventDate"][:10]
    except ValueError:
        return None
    finally:
        if own:
            await client.aclose()
    return None
