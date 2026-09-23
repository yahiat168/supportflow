"""Central configuration. Every key and service URL comes from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _clean(value: str | None) -> str | None:
    """Treat '# comment' leftovers from .env files as empty."""
    if value is None:
        return None
    value = value.strip()
    return None if value.startswith("#") else value


for _k in list(os.environ):
    if os.environ[_k].strip().startswith("#"):
        os.environ[_k] = ""


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))

    # Model provider. "openai" = OpenAI; "gemini" = Google Gemini through its OpenAI-compatible
    # endpoint; "offline" = deterministic, key-free mode used by CI and unit tests.
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    model_api_key: str = field(default_factory=lambda: os.getenv("MODEL_API_KEY", ""))
    model_name: str = field(default_factory=lambda: os.getenv("MODEL_NAME") or (
        "gemini-3.6-flash" if os.getenv("LLM_PROVIDER") == "gemini" else "gpt-4o-mini"))
    model_base_url: str | None = field(default_factory=lambda: os.getenv("MODEL_BASE_URL") or (
        GEMINI_BASE_URL if os.getenv("LLM_PROVIDER") == "gemini" else None))
    # function_calling works on OpenAI and Gemini; json_schema is OpenAI-only strict mode.
    structured_output_method: str = field(
        default_factory=lambda: os.getenv("STRUCTURED_OUTPUT_METHOD", "function_calling"))
    model_timeout_s: float = field(default_factory=lambda: _float("MODEL_TIMEOUT_S", 30))
    model_max_retries: int = field(default_factory=lambda: _int("MODEL_MAX_RETRIES", 2))
    # Waits (seconds) after an HTTP 429 before retrying. Embeddings/ingest use the long list,
    # chat model calls a shorter one so a user is not kept waiting for minutes.
    rate_limit_waits: list[float] = field(default_factory=lambda: [
        float(x) for x in os.getenv("RATE_LIMIT_WAITS", "5,10,20,40,60,60").split(",") if x.strip()])
    chat_rate_limit_waits: list[float] = field(default_factory=lambda: [
        float(x) for x in os.getenv("CHAT_RATE_LIMIT_WAITS", "3,8,15").split(",") if x.strip()])
    ingest_delay_s: float = field(default_factory=lambda: _float("INGEST_DELAY_S", 0))
    # Pause between eval cases, useful on free tiers with low requests-per-minute limits.
    eval_case_delay_s: float = field(default_factory=lambda: _float("EVAL_CASE_DELAY_S", 0))

    embedding_provider: str = field(default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "openai"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL") or (
        "gemini-embedding-001" if os.getenv("EMBEDDING_PROVIDER") == "gemini" else "text-embedding-3-small"))

    # Vector DB. QDRANT_URL=:memory: runs an in-process Qdrant for tests.
    qdrant_url: str = field(default_factory=lambda: os.getenv("QDRANT_URL", "http://localhost:6333"))
    qdrant_api_key: str | None = field(default_factory=lambda: os.getenv("QDRANT_API_KEY") or None)
    qdrant_collection: str = field(default_factory=lambda: os.getenv("QDRANT_COLLECTION", "cloudbox_kb"))
    qdrant_timeout_s: int = field(default_factory=lambda: _int("QDRANT_TIMEOUT_S", 10))

    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://supportflow:supportflow@localhost:5432/supportflow"
        )
    )

    langfuse_public_key: str = field(default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", ""))
    langfuse_secret_key: str = field(default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", ""))
    langfuse_host: str = field(default_factory=lambda: os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))

    frontend_origins: list[str] = field(
        default_factory=lambda: [
            o.strip() for o in os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").split(",") if o.strip()
        ]
    )
    # Lovable previews and published apps live on these domains.
    frontend_origin_regex: str = field(
        default_factory=lambda: os.getenv(
            "FRONTEND_ORIGIN_REGEX", r"https://.*\.(lovable\.app|lovableproject\.com|lovable\.dev)"
        )
    )

    admin_token: str = field(default_factory=lambda: os.getenv("EVALS_ADMIN_TOKEN", ""))

    rag_materials_dir: Path = field(
        default_factory=lambda: Path(os.getenv("RAG_MATERIALS_DIR", str(ROOT_DIR / "rag_materials")))
    )
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data"))))
    auto_ingest: bool = field(default_factory=lambda: _bool("AUTO_INGEST", True))

    # Guardrails / budgets
    retrieval_top_k: int = field(default_factory=lambda: _int("RETRIEVAL_TOP_K", 5))
    retrieval_max_k: int = field(default_factory=lambda: _int("RETRIEVAL_MAX_K", 8))
    # Cosine similarity below this is treated as "no evidence". Offline hashed vectors score lower.
    min_relevance_score: float = field(
        default_factory=lambda: _float(
            "MIN_RELEVANCE_SCORE", {"offline": 0.12, "gemini": 0.55}.get(os.getenv("EMBEDDING_PROVIDER", "openai"), 0.30)
        )
    )
    # Keep only chunks scoring within this distance of the best chunk (trims the noisy tail).
    # 0 disables it; disabled by default for the offline hash embeddings, whose scores are less calibrated.
    relevance_window: float = field(default_factory=lambda: _float(
        "RELEVANCE_WINDOW", 0.0 if os.getenv("EMBEDDING_PROVIDER", "openai") == "offline" else 0.10))
    max_model_calls: int = field(default_factory=lambda: _int("MAX_MODEL_CALLS", 6))
    max_critic_revisions: int = field(default_factory=lambda: _int("MAX_CRITIC_REVISIONS", 1))
    tool_timeout_s: float = field(default_factory=lambda: _float("TOOL_TIMEOUT_S", 5))
    # Knowledge search calls the embedding API, which may wait out rate limits (429), so it gets longer.
    search_timeout_s: float = field(default_factory=lambda: _float("SEARCH_TIMEOUT_S", 45))
    tool_max_retries: int = field(default_factory=lambda: _int("TOOL_MAX_RETRIES", 1))
    graph_recursion_limit: int = field(default_factory=lambda: _int("GRAPH_RECURSION_LIMIT", 25))
    history_turns: int = field(default_factory=lambda: _int("HISTORY_TURNS", 6))

    # Monitoring alert thresholds
    alert_latency_ms: int = field(default_factory=lambda: _int("ALERT_LATENCY_MS", 15000))
    alert_error_rate: float = field(default_factory=lambda: _float("ALERT_ERROR_RATE", 0.10))
    alert_escalation_rate: float = field(default_factory=lambda: _float("ALERT_ESCALATION_RATE", 0.40))
    alert_negative_feedback_rate: float = field(default_factory=lambda: _float("ALERT_NEGATIVE_FEEDBACK_RATE", 0.30))

    @property
    def is_offline(self) -> bool:
        return self.llm_provider == "offline"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
