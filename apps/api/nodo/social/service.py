"""Draft → approval → export: the safe part of the social pipeline, fully local.

Channels are TikTok, Instagram, Facebook (anything else is accepted but labelled). A draft is a `ContentItem`
plus a level-4 `Approval`: Maria can show it, Gaetano says «sì» or «no`, and only then it is exported.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.core.permissions import ActionLevel
from nodo.db.models import Approval, Client, ContentItem, Project

log = logging.getLogger("nodo.social")

CHANNELS = ("tiktok", "instagram", "facebook")

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-")[:60] or "post"


def normalize_channels(raw: str | list[str] | None) -> list[str]:
    """«tiktok e instagram», «ig fb», a list — all become channel names; invalid ones are dropped."""
    if isinstance(raw, list):
        parts = [str(x) for x in raw]
    else:
        parts = re.split(r"[\s,;]+e[\s,;]+|[\s,;/+]+", (raw or "").lower())
    aliases = {"ig": "instagram", "insta": "instagram", "fb": "facebook", "tiktok": "tiktok",
               "facebook": "facebook", "instagram": "instagram", "meta": "facebook",
               "tutti": None, "tutte": None, "social": None}
    out: list[str] = []
    for p in parts:
        p = p.strip().lower()
        ch = aliases.get(p, p)
        if ch and ch in CHANNELS and ch not in out:
            out.append(ch)
    return out or list(CHANNELS)


def draft(s: Session, org: str, *, title: str, body: str, channels: list[str] | None = None,
          client: Client | None = None, project: Project | None = None, kind: str = "post",
          request_id: str = "social") -> tuple[ContentItem, Approval]:
    """Create the post draft and the approval that protects its publication."""
    channels = channels or list(CHANNELS)
    if client is None:                      # personal posts live on a synthetic client row, one per workspace
        client = s.scalars(select(Client).where(Client.organization_id == org, Client.slug == "nodo-social")).first()
        if client is None:
            client = Client(organization_id=org, name="NODO · Social", slug="nodo-social",
                            notes="Contenuti social preparati per Gaetano", aliases=["social", "post"])
            s.add(client)
            s.flush()
    item = ContentItem(client_id=client.id, project_id=project.id if project else None, title=title[:300],
                       kind=kind[:50], channel=", ".join(channels), status="draft", body=body)
    s.add(item)
    s.flush()
    a = Approval(organization_id=org, request_id=request_id, tool_name="social", action="publish",
                 level=int(ActionLevel.EXECUTE_SENSITIVE),
                 payload={"content_item_id": item.id, "channels": channels, "title": item.title},
                 reason=f"Pubblicare su {' + '.join(channels)}: «{item.title}»")
    s.add(a)
    s.flush()
    return item, a


def pending(s: Session, org: str) -> list[dict]:
    out = []
    for a in s.scalars(select(Approval).where(Approval.organization_id == org, Approval.status == "pending",
                                              Approval.tool_name == "social", Approval.action == "publish")):
        item = s.get(ContentItem, (a.payload or {}).get("content_item_id") or "")
        if item is not None and item.status == "draft":
            out.append({"approval_id": a.id, "item_id": item.id, "title": item.title, "body": item.body,
                        "channels": item.channel, "created_at": a.created_at.isoformat()})
    return out


def export_pack(files_dir, item: ContentItem, channels: list[str], settings=None) -> dict:
    """Write the publishable pack to the files folder: caption.txt per channel + meta.json + istruzioni.
    This is how posts ship today (copied by hand or by the phone) and it is what a real publisher would send."""
    from pathlib import Path
    root = Path(files_dir) / "social" / f"{datetime.now(UTC):%Y%m%d}-{_slug(item.title)}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "caption.txt").write_text(f"{item.title}\n\n{item.body or ''}\n", encoding="utf-8")
    meta = {"title": item.title, "kind": item.kind, "channels": channels,
            "exported_at": datetime.now(UTC).isoformat(), "content_item_id": item.id}
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    hints = {"tiktok": "video verticale 9:16, caption ≤ 2200 caratteri",
             "instagram": "immagine/carosello 1:1 o 4:5, hashtag in coda ≤ 30",
             "facebook": "testo + link o immagine, nessun limite di hashtag"}
    (root / "ISTRUZIONI.md").write_text(
        "# Pacchetto post\n\nApri ogni canale e incolla `caption.txt`. Suggerimenti:\n"
        + "".join(f"\n- **{c}**: {hints.get(c, c)}" for c in channels)
        + "\n\nQuando hai pubblicato, dillo a Maria: lo segna come pubblicato.\n", encoding="utf-8")
    return {"path": str(root), "channels": channels}


async def publish_remote(item: ContentItem, channels: list[str], settings) -> dict:
    """SEAM for real API publishing. Today no OAuth tokens exist: every channel reports «token mancante»
    and the export pack is the delivery. When tokens are configured (NODO_TIKTOK_TOKEN, NODO_IG_TOKEN,
    NODO_FB_TOKEN in Accessi) the real uploaders land here without touching the approval flow."""
    results = {}
    for ch in channels:
        token = getattr(settings, f"social_{ch}_token", None)
        if ch in ("instagram", "facebook") and not token:
            token = getattr(settings, "social_meta_token", None)
        results[ch] = {"posted": False, "reason": "token mancante" if not token else "upload non implementato"}
    return {"remote": results, "posted_any": False}
