"""Embedding providers.

* openai  - OpenAI-compatible embeddings (default for the real system).
* offline - deterministic hashed bag-of-words vectors so CI/unit tests run with no keys.
Every call is traced as a Langfuse "embedding" observation.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from functools import lru_cache

from app.config import get_settings
from app.observability.tracing import current_stats, observation

_STOP = set(
    "a an the and or but if of to in on for with at by from is are was were be been it its this that these those "
    "i me my we our you your he she they them their can could do does did how what which who why when where "
    "please tell about there here have has had will would should may might just not no yes so as into than "
    "then also any all some more most other such only own same too very s t get got".split()
)
OFFLINE_DIM = 1024


def _stem(tok: str) -> str:
    for suf in ("ations", "ation", "ated", "ates", "ate", "ing", "ed", "es", "s", "e"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+(?:[._][a-z0-9]+)*", text.lower())
    return [_stem(t) for t in toks if t not in _STOP and len(t) > 1]


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * OFFLINE_DIM
    toks = tokenize(text)
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % OFFLINE_DIM] += (1.0 if (h >> 64) & 1 else -1.0) * (1.0 if "_" not in f else 0.5)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@lru_cache
def _openai_client():
    from langchain_openai import OpenAIEmbeddings

    s = get_settings()
    base_url = s.model_base_url
    if s.embedding_provider == "gemini" and not base_url:
        from app.config import GEMINI_BASE_URL
        base_url = GEMINI_BASE_URL
    return OpenAIEmbeddings(
        model=s.embedding_model, api_key=s.model_api_key, base_url=base_url,
        timeout=s.model_timeout_s, max_retries=1,  # 429s are handled by app.ratelimit (longer waits)
        # Send plain text (not pre-tokenised ids): required by Gemini and other compatible APIs.
        check_embedding_ctx_length=False,
        chunk_size=100,  # Gemini accepts at most 100 texts per request
    )


def _provider() -> str:
    return get_settings().embedding_provider


def embed_texts(texts: list[str], *, purpose: str = "document") -> list[list[float]]:
    settings = get_settings()
    start = time.perf_counter()
    model = settings.embedding_model if _provider() != "offline" else "offline-hash"
    with observation(f"embed_{purpose}", as_type="embedding", model=model,
                     input={"count": len(texts), "preview": texts[0][:200] if texts else ""}) as obs:
        if _provider() == "offline":
            vectors = [_hash_embed(t) for t in texts]
            cached_hits = 0
        else:
            from app.rag import embed_cache
            from app.ratelimit import with_backoff

            vectors = embed_cache.get_many(model, texts)
            missing = [i for i, v in enumerate(vectors) if v is None]
            cached_hits = len(texts) - len(missing)
            if missing:
                client = _openai_client()
                todo = [texts[i] for i in missing]
                if purpose == "document":
                    fresh = with_backoff(lambda: client.embed_documents(todo), what="embeddings")
                else:
                    fresh = [with_backoff(lambda: client.embed_query(todo[0]), waits=settings.chat_rate_limit_waits,
                                          what="embedding")]
                embed_cache.put_many(model, todo, fresh)
                for i, v in zip(missing, fresh):
                    vectors[i] = v
        approx_tokens = sum(len(t) // 4 for t in texts)
        obs.update(output={"dimensions": len(vectors[0]) if vectors else 0, "cache_hits": cached_hits},
                   usage_details={"input": approx_tokens})
    stats = current_stats()
    stats.embedding_calls += 1
    stats.add_time("embedding", (time.perf_counter() - start) * 1000)
    return vectors


def embed_query(text: str) -> list[float]:
    return embed_texts([text], purpose="query")[0]


def embedding_dim() -> int:
    if _provider() == "offline":
        return OFFLINE_DIM
    return len(embed_query("dimension probe"))
