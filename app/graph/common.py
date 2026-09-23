"""Helpers shared by subagents: evidence conversion, prompt formatting, extractive fallbacks."""
from __future__ import annotations

import re
from typing import Any

from app.graph.state import Evidence, SubagentResult
from app.rag.embeddings import tokenize
from app.rag.schemas import RetrievalResult, RetrievedChunk

NO_EVIDENCE_PREFIX = "I couldn't find enough information in the CloudBox knowledge base"


def chunks_to_evidence(chunks: list[RetrievedChunk]) -> list[Evidence]:
    return [Evidence(doc_id=c.doc_id, title=c.title, version=c.version, source_type=c.source_type,
                     trust_level=c.trust_level, content=c.content, score=c.score, section=c.section) for c in chunks]


def retrieval_to_state(result: RetrievalResult) -> dict[str, Any]:
    return {
        "retrieved_context": [c.model_dump() for c in result.chunks],
        "conflicts": [c.model_dump() for c in result.conflicts],
    }


def format_evidence(evidence: list[Evidence], conflicts: list[dict] | None = None) -> str:
    lines = []
    for i, e in enumerate(evidence, 1):
        lines.append(f"[{i}] doc_id={e.doc_id} | title=\"{e.title}\" | version={e.version} | trust={e.trust_level}\n{e.content}")
    for c in conflicts or []:
        lines.append(f"CONFLICT: prefer {c['preferred_doc_id']} (v{c['preferred_version']}) over "
                     f"{c['superseded_doc_id']} (v{c['superseded_version']}): {c['reason']}")
    return "\n\n".join(lines) if lines else "(no evidence)"


def _table_to_sentences(text: str) -> str:
    lines = [l.strip() for l in text.splitlines()]
    rows = [l for l in lines if l.startswith("|")]
    if len(rows) < 3:
        return text
    header = [h.strip() for h in rows[0].strip("|").split("|")]
    out = [l for l in lines if l and not l.startswith("|")]
    for r in rows[2:]:
        cells = [c.strip() for c in r.strip("|").split("|")]
        pairs = ", ".join(f"{h.lower()} {v}" for h, v in zip(header[1:], cells[1:]))
        out.append(f"{cells[0]} plan: {pairs}.")
    return "\n".join(out)


def split_sentences(text: str) -> list[str]:
    text = _table_to_sentences(text)
    parts: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(\d+\.|[-*+])\s+", "", line).replace("**", "").strip()
        if not line:
            continue
        parts.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if s.strip())
    return parts


def extractive_answer(query: str, evidence: list[Evidence], max_sentences: int = 4,
                      exclude_docs: set[str] | None = None) -> tuple[str, list[str]]:
    """Offline composer: pick evidence sentences that best match the query (IDF-weighted overlap)."""
    import math

    q = set(tokenize(query))
    cands: list[tuple[int, int, str, str, set[str]]] = []
    order = 0
    for rank, e in enumerate(evidence):
        if exclude_docs and e.doc_id in exclude_docs:
            continue
        for sent in split_sentences(e.content):
            if sent.rstrip().endswith("?"):
                continue  # FAQ question lines are not answers
            order += 1
            cands.append((rank, order, sent, e.doc_id, set(tokenize(sent))))
    if not cands:
        return "", []
    n = len(cands)
    df = {t: sum(1 for c in cands if t in c[4]) for t in q}
    scored = []
    for rank, order, sent, doc, toks in cands:
        s = sum(math.log(1 + n / df[t]) for t in q & toks if df.get(t))
        if s > 0:
            scored.append((s - 0.1 * rank, order, sent, doc))
    if not scored:
        return "", []
    best = max(x[0] for x in scored)
    top = sorted([x for x in scored if x[0] >= 0.6 * best], key=lambda x: -x[0])[:max_sentences]
    top.sort(key=lambda x: x[1])
    used = list(dict.fromkeys(d for *_, d in top))
    return " ".join(s for _, _, s, _ in top), used


def result_update(result: SubagentResult, **extra: Any) -> dict[str, Any]:
    """Standard state update emitted by every subagent."""
    return {
        "subagent_results": [result.model_dump()],
        "tool_events": result.tool_events,
        "evidence": [e.model_dump() for e in result.evidence],
        "draft_answer": result.draft_answer,
        "used_doc_ids": result.used_doc_ids,
        "missing_information": result.missing_information,
        **extra,
    }


def pinned_evidence(query: str, doc_ids: list[str], top_k: int = 3):
    """Policy-pinned retrieval: search only inside the policy documents that govern this route, and make
    sure each governing document contributes its best chunk, so tool/escalation answers cite their policy."""
    from app.rag import store
    from app.rag.embeddings import embed_query
    from app.rag.schemas import RetrievedChunk, SearchFilters
    from app.tools.support_tools import search_knowledge_base

    res = search_knowledge_base(query, SearchFilters(doc_id=doc_ids), top_k=top_k)
    chunks = list(res.data.chunks) if res.ok else []
    present = {c.doc_id for c in chunks}
    absent = [d for d in doc_ids if d not in present]
    if res.ok and absent:
        vec = embed_query(query)
        for d in absent:
            pts = store.query(vec, {"doc_id": d}, limit=1)
            if pts:
                p = pts[0]
                chunks.append(RetrievedChunk(
                    doc_id=p.payload["doc_id"], title=p.payload["title"], version=str(p.payload["version"]),
                    source_type=p.payload["source_type"], trust_level=p.payload["trust_level"],
                    content=p.payload["content"], score=round(float(p.score), 4),
                    section=p.payload.get("section", "")))
    order = {d: i for i, d in enumerate(doc_ids)}
    chunks.sort(key=lambda c: (order.get(c.doc_id, 99), -c.score))
    return chunks_to_evidence(chunks), res.event.model_dump()
