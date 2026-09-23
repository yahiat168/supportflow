"""Ingestion pipeline: load -> validate metadata -> chunk -> embed -> Qdrant + documents table.

CLI:  python -m app.rag.ingest            (ingest rag_materials/)
      python -m app.rag.ingest --reset    (drop and rebuild the collection)
"""
from __future__ import annotations

import argparse
import logging
import time

from qdrant_client.http import models as qm

from app.config import get_settings
from app.db.models import Document, utcnow
from app.db.session import session_scope
from app.observability.tracing import observation
from app.rag import store
from app.rag.chunker import chunk_markdown, embedding_text
from app.rag.embeddings import embed_texts, embedding_dim
from app.rag.loader import load_directory, parse_markdown
from app.rag.schemas import DocumentMetadata

log = logging.getLogger("supportflow.ingest")


def ingest_document(meta: DocumentMetadata, body: str, filename: str, raw_text: str | None = None) -> int:
    with observation("ingest_document", as_type="span", input={"doc_id": meta.doc_id}) as obs:
        chunks = chunk_markdown(body)
        texts = [embedding_text(meta.title, c.section, c.text) for c in chunks]
        vectors = embed_texts(texts, purpose="document")
        store.ensure_collection(len(vectors[0]))
        store.delete_document(meta.doc_id)  # re-ingest replaces the old version of the same doc
        points = [
            qm.PointStruct(
                id=store.point_id(meta.doc_id, c.index),
                vector=v,
                payload={**meta.model_dump(), "content": c.text, "section": c.section, "chunk_index": c.index},
            )
            for c, v in zip(chunks, vectors)
        ]
        store.upsert(points)
        from app.rag.retriever import invalidate_lexical_cache

        invalidate_lexical_cache()
        with session_scope() as s:
            doc = s.get(Document, meta.doc_id) or Document(doc_id=meta.doc_id)
            doc.title, doc.product, doc.version = meta.title, meta.product, meta.version
            doc.source_type, doc.trust_level, doc.filename = meta.source_type, meta.trust_level, filename
            doc.content = raw_text if raw_text is not None else body
            doc.chunk_count, doc.indexed_at = len(chunks), utcnow()
            s.merge(doc)
        obs.update(output={"chunks": len(chunks)})
        return len(chunks)


def ingest_text(raw_text: str, filename: str) -> tuple[DocumentMetadata, int]:
    meta, body = parse_markdown(raw_text)
    return meta, ingest_document(meta, body, filename, raw_text)


def ingest_directory(reset: bool = False) -> dict[str, int]:
    settings = get_settings()
    if reset and store.collection_exists():
        store.get_client().delete_collection(store.collection_name())
    store.ensure_collection(embedding_dim())
    results = {}
    for i, (meta, body, filename) in enumerate(load_directory(settings.rag_materials_dir)):
        if i and settings.ingest_delay_s:
            time.sleep(settings.ingest_delay_s)  # gentle on rate-limited keys
        results[meta.doc_id] = ingest_document(meta, body, filename)
        log.info("indexed %s (%d chunks)", meta.doc_id, results[meta.doc_id])
    return results


if __name__ == "__main__":
    from app.db.session import init_db

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    init_db()
    res = ingest_directory(reset=args.reset)
    print(f"Indexed {len(res)} documents, {sum(res.values())} chunks: {res}")
