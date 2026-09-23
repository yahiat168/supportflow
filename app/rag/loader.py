"""Markdown loader that preserves the metadata block at the top of each document."""
from __future__ import annotations

import re
from pathlib import Path

from app.rag.schemas import DocumentMetadata

REQUIRED_FIELDS = ["doc_id", "title", "product", "version", "source_type", "trust_level"]
_FRONT_MATTER = re.compile(r"^\ufeff?---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class DocumentFormatError(ValueError):
    pass


def parse_markdown(text: str) -> tuple[DocumentMetadata, str]:
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if not match:
        raise DocumentFormatError("Document must start with a '---' metadata block.")
    raw_meta, body = match.groups()
    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
    if missing:
        raise DocumentFormatError(f"Missing metadata fields: {', '.join(missing)}")
    if not re.fullmatch(r"[a-z0-9_\-]+", meta["doc_id"]):
        raise DocumentFormatError("doc_id must be lowercase letters, digits, '_' or '-'.")
    return DocumentMetadata(**{k: meta[k] for k in REQUIRED_FIELDS}), body.strip()


def load_directory(directory: Path) -> list[tuple[DocumentMetadata, str, str]]:
    """Return (metadata, body, filename) for every KB document. README.md is skipped."""
    docs = []
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        meta, body = parse_markdown(path.read_text(encoding="utf-8"))
        docs.append((meta, body, path.name))
    return docs
