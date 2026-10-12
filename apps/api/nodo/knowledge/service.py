"""Import, index and search personal knowledge. Everything here is local and deterministic.

A `KnowledgeSource` is one import; each conversation becomes a `Document` (kind `chat:<provider>`, uri
`<provider>:<external id>`) whose text is sliced into `KnowledgeChunk`s mirrored in an FTS5 table. Re-importing
a newer export updates only the conversations that grew; nothing is deleted unless you remove a source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from nodo.db.models import Document, FactStatus, KnowledgeChunk, KnowledgeSource, MemoryFact, Sensitivity
from nodo.knowledge.importers import ImportedConversation, chunk_turns

_FTS = "knowledge_fts"
_WORD = re.compile(r"[\wàèéìòù]+", re.UNICODE)


@dataclass
class ImportStats:
    conversations: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    chunks: int = 0
    chars: int = 0

    def as_dict(self) -> dict:
        return {"conversations": self.conversations, "new": self.new, "updated": self.updated,
                "unchanged": self.unchanged, "chunks": self.chunks, "chars": self.chars}


def is_sqlite(s: Session) -> bool:
    return s.get_bind().dialect.name == "sqlite"


def ensure_index(s: Session) -> bool:
    """Creates the FTS5 mirror once (SQLite only). Other databases fall back to LIKE search."""
    if not is_sqlite(s):
        return False
    s.execute(sql(f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS} USING fts5(chunk_id UNINDEXED, body, "
                  "tokenize='unicode61 remove_diacritics 2')"))
    return True


class KnowledgeService:
    def __init__(self, session: Session, organization_id: str):
        self.s, self.org = session, organization_id
        self.fts = ensure_index(session)

    # ------------------------------------------------------------------------------------ import
    def import_conversations(self, convs: list[ImportedConversation], *, kind: str, label: str,
                             sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL) -> tuple[KnowledgeSource, ImportStats]:
        src = KnowledgeSource(organization_id=self.org, kind=kind, label=label[:300], sensitivity=sensitivity)
        self.s.add(src)
        self.s.flush()
        st = ImportStats()
        existing = {d.uri: d for d in self.s.scalars(select(Document).where(
            Document.organization_id == self.org, Document.kind == f"chat:{kind}"))}
        for conv in convs:
            if not conv.turns:
                continue
            st.conversations += 1
            uri = f"{kind}:{conv.external_id or conv.title}"
            doc = existing.get(uri)
            n_turns = len(conv.turns)
            if doc is not None:
                prev = self.s.scalar(select(func.count()).select_from(KnowledgeChunk)
                                     .where(KnowledgeChunk.document_id == doc.id)) or 0
                prev_turns = int(_meta(doc).get("turns", -1))
                if prev and prev_turns == n_turns:
                    st.unchanged += 1
                    continue
                self._drop_chunks(doc.id)
                st.updated += 1
            else:
                doc = Document(organization_id=self.org, title=conv.title, kind=f"chat:{kind}", uri=uri,
                               sensitivity=sensitivity)
                self.s.add(doc)
                self.s.flush()
                existing[uri] = doc
                st.new += 1
            doc.summary = _summary_line(conv, n_turns)
            for seq, (body, at) in enumerate(chunk_turns(conv.turns)):
                ch = KnowledgeChunk(organization_id=self.org, source_id=src.id, document_id=doc.id, seq=seq,
                                    text=body, happened_at=at or conv.created_at)
                self.s.add(ch)
                self.s.flush()
                if self.fts:
                    self.s.execute(sql(f"INSERT INTO {_FTS}(chunk_id, body) VALUES (:id, :body)"),
                                   {"id": ch.id, "body": f"{conv.title}\n{body}"})
                st.chunks += 1
                st.chars += len(body)
        src.stats = {**st.as_dict(), "distilled": 0, "facts": 0, "skipped": 0}
        self.s.flush()
        return src, st

    def _drop_chunks(self, document_id: str) -> None:
        ids = list(self.s.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document_id)))
        if ids and self.fts:
            for i in range(0, len(ids), 500):
                batch = ids[i:i + 500]
                marks = ",".join(f":p{k}" for k in range(len(batch)))
                self.s.execute(sql(f"DELETE FROM {_FTS} WHERE chunk_id IN ({marks})"),
                               {f"p{k}": v for k, v in enumerate(batch)})
        self.s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))

    def remove_source(self, source_id: str) -> bool:
        """Removes imported text. Facts distilled from it are archived, not deleted (memory is append-only)."""
        src = self.s.get(KnowledgeSource, source_id)
        if src is None or src.organization_id != self.org:
            return False
        doc_ids = set(self.s.scalars(select(KnowledgeChunk.document_id).where(KnowledgeChunk.source_id == source_id)))
        for d in doc_ids:
            self._drop_chunks(d)
        for f in self.s.scalars(select(MemoryFact).where(MemoryFact.organization_id == self.org,
                                                        MemoryFact.source_type == "document",
                                                        MemoryFact.source_id.in_(doc_ids) if doc_ids else False)):
            f.status, f.valid_until = FactStatus.ARCHIVED, datetime.now(UTC)
        for d in doc_ids:
            still = self.s.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == d))
            if not still and (doc := self.s.get(Document, d)):
                self.s.delete(doc)
        self.s.delete(src)
        self.s.flush()
        return True

    # ------------------------------------------------------------------------------------ search
    def search(self, query: str, limit: int = 6, kind: str | None = None) -> list[dict]:
        words = [w for w in _WORD.findall(query.lower()) if len(w) >= 2][:12]
        if not words:
            return []
        rows: list[tuple[KnowledgeChunk, str]] = []
        if self.fts:
            for match in (" AND ".join(f'"{w}"' for w in words), " OR ".join(f'"{w}"' for w in words)):
                res = self.s.execute(sql(
                    f"SELECT chunk_id, snippet({_FTS}, 1, '', '', '…', 36) FROM {_FTS} "
                    f"WHERE {_FTS} MATCH :q ORDER BY bm25({_FTS}) LIMIT :n"), {"q": match, "n": limit * 3}).all()
                if res:
                    by_id = {c.id: c for c in self.s.scalars(select(KnowledgeChunk).where(
                        KnowledgeChunk.id.in_([r[0] for r in res]), KnowledgeChunk.organization_id == self.org))}
                    rows = [(by_id[r[0]], r[1]) for r in res if r[0] in by_id]
                    break
        else:
            q = select(KnowledgeChunk).where(KnowledgeChunk.organization_id == self.org)
            for w in words[:4]:
                q = q.where(KnowledgeChunk.text.ilike(f"%{w}%"))
            rows = [(c, _around(c.text, words)) for c in self.s.scalars(q.limit(limit * 3))]
        out, seen_docs = [], set()
        docs = {d.id: d for d in self.s.scalars(select(Document).where(Document.id.in_([c.document_id for c, _ in rows])))} \
            if rows else {}
        for c, snippet in rows:
            d = docs.get(c.document_id)
            if d is None or (kind and d.kind != f"chat:{kind}"):
                continue
            if c.document_id in seen_docs and len(out) >= 3:
                continue  # prefer breadth across conversations once we have a few hits
            seen_docs.add(c.document_id)
            out.append({"title": d.title, "source": d.kind.split(":", 1)[-1], "when": _day(c.happened_at),
                        "snippet": snippet.strip()[:600], "chunk_id": c.id, "document_id": d.id})
            if len(out) >= limit:
                break
        return out

    def chunk_text(self, chunk_id: str) -> str | None:
        c = self.s.get(KnowledgeChunk, chunk_id)
        return c.text if c and c.organization_id == self.org else None

    # ------------------------------------------------------------------------------------ overview
    def sources(self) -> list[KnowledgeSource]:
        return list(self.s.scalars(select(KnowledgeSource).where(KnowledgeSource.organization_id == self.org)
                                   .order_by(KnowledgeSource.created_at.desc())))

    def overview(self) -> dict:
        n_docs = self.s.scalar(select(func.count()).select_from(Document).where(
            Document.organization_id == self.org, Document.kind.like("chat:%"))) or 0
        n_chunks = self.s.scalar(select(func.count()).select_from(KnowledgeChunk)
                                 .where(KnowledgeChunk.organization_id == self.org)) or 0
        n_facts = self.s.scalar(select(func.count()).select_from(MemoryFact).where(
            MemoryFact.organization_id == self.org, MemoryFact.source_type == "document",
            MemoryFact.status.in_((FactStatus.CONFIRMED, FactStatus.INFERRED, FactStatus.UNVERIFIED)))) or 0
        return {"conversations": n_docs, "chunks": n_chunks, "facts": n_facts}


def _meta(doc: Document) -> dict:
    m = re.search(r"\[(\d+) turni\]", doc.summary or "")
    return {"turns": int(m.group(1))} if m else {}


def _summary_line(conv: ImportedConversation, n_turns: int) -> str:
    first = next((t.text for t in conv.turns if t.role == "user"), "")
    when = conv.created_at.date().isoformat() if conv.created_at else "data sconosciuta"
    return f"[{n_turns} turni] {when} — {first[:240]}"


def _day(d: datetime | None) -> str | None:
    return d.date().isoformat() if d else None


def _around(text_: str, words: list[str], width: int = 240) -> str:
    low = text_.lower()
    pos = min((p for p in (low.find(w) for w in words) if p >= 0), default=0)
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text_[start:start + width] + ("…" if start + width < len(text_) else "")
