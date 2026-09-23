"""Retriever: filtered vector search + trust/version-aware ranking + conflict detection."""
from __future__ import annotations

import math
import time

from app.config import get_settings
from app.observability.tracing import current_stats, observation
from app.rag import store
from app.rag.embeddings import embed_query
from app.rag.schemas import RetrievalResult, RetrievedChunk, SearchFilters, SourceConflict
from app.rag.versioning import TRUST_RANK, compare_versions

# Community / unverified material is never used unless a caller asks for it explicitly.
DEFAULT_TRUST = ["official", "internal"]
MAX_CHUNKS_PER_DOC = 2


def _rank_key(c: RetrievedChunk) -> float:
    # Small, bounded boosts: relevance dominates, trust breaks near-ties.
    return c.score + 0.02 * TRUST_RANK.get(c.trust_level, 0)


def detect_conflicts(chunks: list[RetrievedChunk]) -> list[SourceConflict]:
    """Two different documents of the same product + source_type with different versions
    cover the same ground (e.g. an archived 3.2 catalogue vs the current 3.4 catalogue)."""
    conflicts: list[SourceConflict] = []
    seen: set[tuple[str, str]] = set()
    docs = {c.doc_id: c for c in chunks}
    for a in docs.values():
        for b in docs.values():
            if a.doc_id >= b.doc_id or (a.doc_id, b.doc_id) in seen:
                continue
            if a.product != b.product or a.source_type != b.source_type or a.version == b.version:
                continue
            cmp = compare_versions(a.version, b.version)
            ta, tb = TRUST_RANK.get(a.trust_level, 0), TRUST_RANK.get(b.trust_level, 0)
            if ta != tb:
                newer, older = (a, b) if ta > tb else (b, a)
                reason = f"'{newer.title}' has a higher trust level ({newer.trust_level})"
            elif cmp is not None and cmp != 0:
                newer, older = (a, b) if cmp > 0 else (b, a)
                reason = f"'{newer.title}' is the newer version ({newer.version} > {older.version})"
            else:
                continue
            seen.add((a.doc_id, b.doc_id))
            conflicts.append(SourceConflict(
                preferred_doc_id=newer.doc_id, preferred_version=newer.version,
                superseded_doc_id=older.doc_id, superseded_version=older.version, reason=reason,
            ))
    return conflicts


# ---------- lexical side of hybrid search (BM25 over the small KB corpus) ----------
_lex_cache: dict = {"count": -1, "docs": []}
LEXICAL_WEIGHT = 0.15


def invalidate_lexical_cache() -> None:
    _lex_cache["count"] = -1


def _lexical_corpus() -> list[dict]:
    from app.rag.embeddings import tokenize

    n = store.count_points()
    if _lex_cache["count"] != n:
        docs, offset = [], None
        while True:
            pts, offset = store.get_client().scroll(store.collection_name(), limit=256, offset=offset,
                                                    with_payload=True, with_vectors=False)
            for p in pts:
                text = f"{p.payload['title']} {p.payload.get('section', '')} {p.payload['content']}"
                docs.append({"id": p.id, "payload": p.payload, "tokens": tokenize(text)})
            if offset is None:
                break
        _lex_cache.update(count=n, docs=docs)
    return _lex_cache["docs"]


def _matches(payload: dict, filters: dict) -> bool:
    for key, value in filters.items():
        if value in (None, [], ""):
            continue
        allowed = value if isinstance(value, list) else [value]
        if str(payload.get(key)) not in [str(v) for v in allowed]:
            return False
    return True


def lexical_scores(query: str, filters: dict) -> dict[str, float]:
    """BM25 scores normalised to 0..1, keyed by point id. Rewards rare exact terms (names, versions, IDs)."""
    from app.rag.embeddings import tokenize

    docs = [d for d in _lexical_corpus() if _matches(d["payload"], filters)]
    q = set(tokenize(query))
    if not docs or not q:
        return {}
    n = len(_lex_cache["docs"])
    avgdl = sum(len(d["tokens"]) for d in _lex_cache["docs"]) / max(n, 1)
    df = {t: sum(1 for d in _lex_cache["docs"] if t in d["tokens"]) for t in q}
    scores = {}
    for d in docs:
        tf_total, dl = 0.0, len(d["tokens"])
        for t in q:
            tf = d["tokens"].count(t)
            if tf and df[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                tf_total += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / avgdl))
        if tf_total > 0:
            scores[str(d["id"])] = tf_total
    top = max(scores.values(), default=0) or 1.0
    return {k: v / top for k, v in scores.items()}


def search(query: str, filters: SearchFilters | None = None, top_k: int | None = None) -> RetrievalResult:
    settings = get_settings()
    filters = filters or SearchFilters()
    if filters.trust_level is None:
        filters = filters.model_copy(update={"trust_level": DEFAULT_TRUST})
    k = min(top_k or settings.retrieval_top_k, settings.retrieval_max_k)
    start = time.perf_counter()
    with observation("retrieve", as_type="retriever",
                     input={"query": query, "filters": filters.model_dump(exclude_none=True), "top_k": k}) as obs:
        vector = embed_query(query)
        fdict = filters.model_dump(exclude_none=True)
        points = store.query(vector, fdict, limit=k * 3)
        lex = lexical_scores(query, fdict)
        dense = {str(p.id): (float(p.score), p.payload) for p in points}
        # Lexical-only candidates: fetch their vectors so they get a real dense score too.
        missing = [pid for pid, _ in sorted(lex.items(), key=lambda x: -x[1])[:k] if pid not in dense]
        if missing:
            for p in store.get_client().retrieve(store.collection_name(), ids=missing, with_payload=True,
                                                 with_vectors=True):
                cos = sum(a * b for a, b in zip(vector, p.vector)) / (
                    (math.sqrt(sum(a * a for a in vector)) * math.sqrt(sum(b * b for b in p.vector))) or 1.0)
                dense[str(p.id)] = (cos, p.payload)
        chunks = [
            RetrievedChunk(
                doc_id=pl["doc_id"], title=pl["title"], version=str(pl["version"]),
                source_type=pl["source_type"], trust_level=pl["trust_level"],
                content=pl["content"], score=round(cos + LEXICAL_WEIGHT * lex.get(pid, 0.0), 4),
                product=pl.get("product", ""), section=pl.get("section", ""),
                chunk_index=pl.get("chunk_index", 0),
            )
            for pid, (cos, pl) in dense.items()
        ]
        relevant = [c for c in chunks if c.score >= settings.min_relevance_score]
        relevant.sort(key=_rank_key, reverse=True)
        if relevant and settings.relevance_window > 0:
            best = relevant[0].score
            relevant = [c for c in relevant if c.score >= best - settings.relevance_window]
        per_doc: dict[str, int] = {}
        selected: list[RetrievedChunk] = []
        for c in relevant:
            if per_doc.get(c.doc_id, 0) >= MAX_CHUNKS_PER_DOC:
                continue
            per_doc[c.doc_id] = per_doc.get(c.doc_id, 0) + 1
            selected.append(c)
            if len(selected) >= k:
                break
        conflicts = detect_conflicts(selected)
        if conflicts:
            # Superseded (older / less trusted) sources go to the bottom: still available for the
            # "an older source differs" note, but never ranked above the current source.
            superseded = {c.superseded_doc_id for c in conflicts}
            selected.sort(key=lambda c: c.doc_id in superseded)
        result = RetrievalResult(query=query, chunks=selected, conflicts=conflicts, filters=filters,
                                 insufficient=not selected)
        obs.update(output={
            "results": [{"doc_id": c.doc_id, "section": c.section, "score": c.score} for c in selected],
            "conflicts": [c.model_dump() for c in conflicts], "insufficient": result.insufficient,
        })
    current_stats().add_time("retrieval", (time.perf_counter() - start) * 1000)
    return result
