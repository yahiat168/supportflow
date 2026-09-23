"""Test configuration: fully offline (no API keys, in-memory Qdrant, temporary SQLite)."""
import os
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="supportflow_test_"), "test.db")
os.environ.update({
    "APP_ENV": "test", "LLM_PROVIDER": "offline", "EMBEDDING_PROVIDER": "offline", "QDRANT_URL": ":memory:",
    "DATABASE_URL": f"sqlite:///{_DB}", "TOOL_TIMEOUT_S": "0.5", "TOOL_MAX_RETRIES": "1",
    "EVALS_ADMIN_TOKEN": "test-admin-token", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "",
    "AUTO_INGEST": "true", "STATUS_SCENARIO": "operational",
    # Pin everything a real .env might set, so tests never depend on your local configuration.
    "MIN_RELEVANCE_SCORE": "0.12", "RELEVANCE_WINDOW": "0", "EVAL_CASE_DELAY_S": "0", "INGEST_DELAY_S": "0",
    "MODEL_BASE_URL": "", "MODEL_NAME": "gpt-4o-mini", "EMBEDDING_MODEL": "text-embedding-3-small",
    "MODEL_API_KEY": "", "RATE_LIMIT_WAITS": "0.01", "CHAT_RATE_LIMIT_WAITS": "0.01",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ADMIN = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def chat(client):
    def _chat(message, user_id="user_maya", thread_id=None, headers=None, expect=200):
        r = client.post("/chat", json={"user_id": user_id, "thread_id": thread_id, "message": message},
                        headers=headers or {})
        assert r.status_code == expect, r.text
        return r.json()
    return _chat
