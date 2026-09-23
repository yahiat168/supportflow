"""Qdrant vector store wrapper."""
from __future__ import annotations

import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import get_settings

FILTER_FIELDS = ["doc_id", "product", "version", "source_type", "trust_level"]


@lru_cache
def get_client() -> QdrantClient:
    s = get_settings()
    if s.qdrant_url == ":memory:":
        return QdrantClient(location=":memory:")
    return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key, timeout=s.qdrant_timeout_s)


def reset_client() -> None:
    get_client.cache_clear()


def collection_name() -> str:
    return get_settings().qdrant_collection


def ensure_collection(dim: int) -> None:
    client = get_client()
    name = collection_name()
    if not client.collection_exists(name):
        client.create_collection(name, vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE))
        for f in FILTER_FIELDS:
            try:
                client.create_payload_index(name, field_name=f, field_schema=qm.PayloadSchemaType.KEYWORD)
            except Exception:
                pass  # local/in-memory mode does not need indexes


def collection_exists() -> bool:
    return get_client().collection_exists(collection_name())


def count_points() -> int:
    if not collection_exists():
        return 0
    return get_client().count(collection_name(), exact=True).count


def point_id(doc_id: str, idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"supportflow/{doc_id}/{idx}"))


def delete_document(doc_id: str) -> None:
    if not collection_exists():
        return
    get_client().delete(
        collection_name(),
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
        ),
    )


def upsert(points: list[qm.PointStruct]) -> None:
    get_client().upsert(collection_name(), points=points, wait=True)


def build_filter(filters: dict) -> qm.Filter | None:
    must = []
    for key, value in filters.items():
        if value in (None, [], ""):
            continue
        if isinstance(value, list):
            must.append(qm.FieldCondition(key=key, match=qm.MatchAny(any=value)))
        else:
            must.append(qm.FieldCondition(key=key, match=qm.MatchValue(value=value)))
    return qm.Filter(must=must) if must else None


def query(vector: list[float], filters: dict, limit: int) -> list[qm.ScoredPoint]:
    return get_client().query_points(
        collection_name(), query=vector, query_filter=build_filter(filters), limit=limit, with_payload=True,
    ).points


def health() -> str:
    try:
        get_client().get_collections()
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"
