"""Tiny on-disk embedding cache (SQLite).

Every embedding received from the provider is stored by hash(model + text). Re-running ingestion or
asking the same question again never calls the API twice for the same text, which matters a lot on
rate-limited keys. Delete the .cache folder to clear it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from app.config import ROOT_DIR

_DB = ROOT_DIR / ".cache" / "embeddings.sqlite"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB), check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v TEXT)")
    return _conn


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()


def get_many(model: str, texts: list[str]) -> list[list[float] | None]:
    with _lock:
        out = []
        for t in texts:
            row = _db().execute("SELECT v FROM emb WHERE k=?", (_key(model, t),)).fetchone()
            out.append(json.loads(row[0]) if row else None)
        return out


def put_many(model: str, texts: list[str], vectors: list[list[float]]) -> None:
    with _lock:
        _db().executemany("INSERT OR REPLACE INTO emb (k, v) VALUES (?, ?)",
                          [(_key(model, t), json.dumps(v)) for t, v in zip(texts, vectors)])
        _db().commit()
