"""Readers for personal exports. Each yields `ImportedConversation`s: a title, a date and ordered turns.

Supported today: the official data exports of ChatGPT (`conversations.json`, tree of nodes) and Claude
(`conversations.json`, flat `chat_messages`). Both arrive as a .zip; a bare .json works too.
No network, no model: parsing is deterministic and offline.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Turn:
    role: str              # user | assistant
    text: str
    at: datetime | None = None


@dataclass
class ImportedConversation:
    external_id: str
    title: str
    kind: str              # chatgpt | claude
    created_at: datetime | None
    turns: list[Turn] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(t.text) for t in self.turns)


class UnknownExport(ValueError):
    pass


# ------------------------------------------------------------------------------------------- entry points
def read_export(data: bytes | Path, filename: str = "") -> list[ImportedConversation]:
    """Accepts a .zip (official export) or a conversations.json; detects ChatGPT vs Claude from the shape.

    Recent ChatGPT exports split the chats into conversations-000.json, conversations-001.json, ... and ship several
    GB of attachments next to them: a zip on disk is opened in place and only the conversation files are read."""
    name = filename or (data.name if isinstance(data, Path) else "")
    if isinstance(data, Path):
        with open(data, "rb") as fh:
            is_zip = fh.read(2) == b"PK"
        if is_zip:
            with zipfile.ZipFile(data) as z:
                return _from_zip(z)
        raw = data.read_bytes()
    else:
        raw = data
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                return _from_zip(z)
    return list(parse_conversations(_json(raw, name)))


_PART = re.compile(r"(?:^|/)conversations(?:-(\d+))?\.json$")


def _from_zip(z: zipfile.ZipFile) -> list[ImportedConversation]:
    names = [n for n in z.namelist() if _PART.search(n)]
    whole = [n for n in names if n.rsplit("/", 1)[-1] == "conversations.json"]
    if whole:                                             # classic export: one file (shortest path = top level)
        parts = [sorted(whole, key=len)[0]]
    else:
        parts = sorted(names, key=lambda n: int(_PART.search(n).group(1) or 0))
    if not parts:
        raise UnknownExport("nello zip non c'è conversations.json: non sembra un export di ChatGPT o Claude")
    out: list[ImportedConversation] = []
    for part in parts:
        payload = _json(z.read(part), part)
        out.extend(parse_conversations(payload))
    return out


def _json(raw: bytes, name: str):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UnknownExport(f"il file {name or 'caricato'} non è un JSON leggibile: {e}") from e


def detect_kind(payload) -> str:
    items = payload if isinstance(payload, list) else [payload]
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        if "mapping" in it:
            return "chatgpt"
        if "chat_messages" in it:
            return "claude"
    raise UnknownExport("formato non riconosciuto: servono le conversazioni esportate da ChatGPT o da Claude")


def parse_conversations(payload) -> Iterator[ImportedConversation]:
    kind = detect_kind(payload)
    items = payload if isinstance(payload, list) else [payload]
    parser = _chatgpt if kind == "chatgpt" else _claude
    for it in items:
        if isinstance(it, dict):
            conv = parser(it)
            if conv and conv.turns:
                yield conv


# ------------------------------------------------------------------------------------------- ChatGPT
def _ts(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), UTC)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _chatgpt_text(message: dict) -> str:
    content = message.get("content") or {}
    ctype = content.get("content_type", "text")
    if ctype in ("text", "multimodal_text"):
        parts = []
        for p in content.get("parts") or []:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and p.get("text"):
                parts.append(str(p["text"]))
        return "\n".join(parts).strip()
    if ctype == "code":
        return (content.get("text") or "").strip()
    if ctype == "user_editable_context":
        return ""  # custom instructions block: handled as preferences elsewhere, not a turn
    return (content.get("text") or content.get("result") or "").strip() if isinstance(content, dict) else ""


def _chatgpt(item: dict) -> ImportedConversation | None:
    mapping = item.get("mapping") or {}
    if not mapping:
        return None
    # Follow the active branch from the final node up to the root: edits and regenerations leave dead branches.
    node_id = item.get("current_node")
    if node_id not in mapping:
        leaves = [k for k, v in mapping.items() if not (v.get("children") or [])]
        node_id = leaves[-1] if leaves else None
    chain: list[dict] = []
    seen: set[str] = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if node.get("message"):
            chain.append(node["message"])
        node_id = node.get("parent")
    chain.reverse()
    turns: list[Turn] = []
    for m in chain:
        role = ((m.get("author") or {}).get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        if (m.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        text = _chatgpt_text(m)
        if text:
            turns.append(Turn(role, text, _ts(m.get("create_time"))))
    return ImportedConversation(external_id=str(item.get("conversation_id") or item.get("id") or ""),
                                title=(item.get("title") or "Senza titolo").strip()[:300], kind="chatgpt",
                                created_at=_ts(item.get("create_time")), turns=_merge_consecutive(turns))


# ------------------------------------------------------------------------------------------- Claude
def _claude(item: dict) -> ImportedConversation | None:
    msgs = item.get("chat_messages")
    if not isinstance(msgs, list):
        return None
    turns: list[Turn] = []
    for m in msgs:
        role = "user" if (m.get("sender") or "").lower() == "human" else "assistant"
        text = (m.get("text") or "").strip()
        if not text:
            text = "\n".join(str(c.get("text") or "") for c in (m.get("content") or [])
                             if isinstance(c, dict) and c.get("type") == "text").strip()
        if text:
            turns.append(Turn(role, text, _ts(m.get("created_at"))))
    return ImportedConversation(external_id=str(item.get("uuid") or ""), kind="claude",
                                title=(item.get("name") or "Senza titolo").strip()[:300],
                                created_at=_ts(item.get("created_at")), turns=_merge_consecutive(turns))


def _merge_consecutive(turns: Iterable[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for t in turns:
        if out and out[-1].role == t.role:
            out[-1].text += "\n" + t.text
        else:
            out.append(Turn(t.role, t.text, t.at))
    return out


# ------------------------------------------------------------------------------------------- chunking
def chunk_turns(turns: list[Turn], size: int = 1800, who: tuple[str, str] = ("Gaetano", "assistente")) -> list[tuple[str, datetime | None]]:
    """Groups consecutive turns into ~`size`-character slices, never splitting a short turn; very long turns are
    cut at paragraph boundaries. Returns (text, time of first turn in the slice)."""
    out: list[tuple[str, datetime | None]] = []
    buf, buf_at = "", None
    for t in turns:
        label = who[0] if t.role == "user" else who[1]
        for piece in _split(t.text, size):
            line = f"{label}: {piece}"
            if buf and len(buf) + len(line) + 1 > size:
                out.append((buf, buf_at))
                buf, buf_at = "", None
            buf_at = buf_at or t.at
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append((buf, buf_at))
    return out


def _split(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for para in text.split("\n"):
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        if cur and len(cur) + len(para) + 1 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out
