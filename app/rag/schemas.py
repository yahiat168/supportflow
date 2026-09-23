from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    doc_id: str
    title: str
    product: str
    version: str
    source_type: str
    trust_level: str


class RetrievedChunk(BaseModel):
    """Exact shape required by the brief (plus optional section/chunk index)."""
    doc_id: str
    title: str
    version: str
    source_type: str
    trust_level: str
    content: str
    score: float
    product: str = ""
    section: str = ""
    chunk_index: int = 0


class SearchFilters(BaseModel):
    product: str | None = None
    version: str | None = None
    source_type: list[str] | None = None
    trust_level: list[str] | None = None
    doc_id: list[str] | None = None


class SourceConflict(BaseModel):
    preferred_doc_id: str
    preferred_version: str
    superseded_doc_id: str
    superseded_version: str
    reason: str


class RetrievalResult(BaseModel):
    query: str
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    conflicts: list[SourceConflict] = Field(default_factory=list)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    insufficient: bool = False
