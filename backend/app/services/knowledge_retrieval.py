"""Deterministic lexical RAG without additional dependencies or external indexing."""
import re
import unicodedata
from dataclasses import dataclass
from collections.abc import Mapping


STOP_WORDS = set("toi ban cua la va cho ve co gi the nao nhung mot trong duoc hay xin hoi cong ty quy dinh chinh sach".split())


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text))) - STOP_WORDS


@dataclass
class Passage:
    document_id: int
    title: str
    content: str
    source_url: str | None
    version_id: int | None = None
    section_id: int | None = None
    section_code: str | None = None
    heading: str | None = None
    page_start: int | None = None
    page_end: int | None = None


def _field(record, name, default=None):
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)


def full_policy_context(documents, max_chars: int) -> list[Passage]:
    """Read an already authorized corpus whole, or let lexical retrieval handle it.

    Never truncate sections or silently omit the tail of an oversized corpus.
    Role, current-version and effective-date checks belong to AIKnowledgeTool.
    """
    if max_chars <= 0:
        return []
    passages = []
    size = 0
    for doc in documents:
        if _field(doc, "status") != "PUBLISHED" or not _field(doc, "is_answerable", True):
            continue
        content = _field(doc, "content", "")
        if not content.strip():
            continue
        size += len(content) + len(_field(doc, "title", "")) + len(_field(doc, "heading") or "")
        if size > max_chars:
            return []
        passages.append(Passage(**{
            name: _field(doc, name) for name in Passage.__dataclass_fields__
        }))
    return passages


def retrieve(question: str, documents, limit: int = 3) -> list[Passage]:
    if limit <= 0:
        return []
    query = tokens(question)
    if not query:
        return []
    ranked = []
    for doc in documents:
        if _field(doc, "status") != "PUBLISHED" or not _field(doc, "is_answerable", True):
            continue
        content = _field(doc, "content", "")
        title = _field(doc, "title", "")
        section_metadata = {
            "version_id": _field(doc, "version_id"),
            "section_id": _field(doc, "section_id"),
            "section_code": _field(doc, "section_code"),
            "heading": _field(doc, "heading"),
            "page_start": _field(doc, "page_start"),
            "page_end": _field(doc, "page_end"),
        }
        # Overlap preserves sentences crossing a chunk boundary.
        for start in range(0, len(content), 1000):
            chunk = content[start:start + 1200]
            # Titles boost ranking but cannot make an unrelated passage evidence.
            overlap = query & tokens(chunk)
            if not overlap or len(overlap) / len(query) < 0.35:
                continue
            score = len(overlap) + len(query & tokens(title)) + 2 * len(query & tokens(section_metadata["heading"] or ""))
            ranked.append((score, Passage(
                document_id=_field(doc, "document_id"),
                title=title,
                content=chunk,
                source_url=_field(doc, "source_url"),
                **section_metadata,
            )))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = []
    seen = set()
    for _, passage in ranked:
        # A PDF section links to one location; show its best matching excerpt once.
        key = (passage.document_id, passage.version_id, passage.section_id)
        if passage.section_id is not None and key in seen:
            continue
        seen.add(key)
        selected.append(passage)
        if len(selected) >= limit:
            break
    return selected
