"""Langfuse instrumentation (SDK v4, OpenTelemetry based).

Everything degrades to a no-op when LANGFUSE_* keys are absent, so tests and offline
runs never depend on the monitoring service. Monitoring failures are logged, never raised.
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.config import get_settings

log = logging.getLogger("supportflow.tracing")
_client = None
_client_failed = False


@dataclass
class RequestStats:
    request_id: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_calls: int = 0
    tool_calls: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add_time(self, bucket: str, ms: float) -> None:
        self.timings_ms[bucket] = round(self.timings_ms.get(bucket, 0.0) + ms, 1)


_stats: contextvars.ContextVar[RequestStats | None] = contextvars.ContextVar("sf_stats", default=None)


def current_stats() -> RequestStats:
    s = _stats.get()
    if s is None:
        s = RequestStats()
        _stats.set(s)
    return s


def new_stats(request_id: str) -> RequestStats:
    s = RequestStats(request_id=request_id)
    _stats.set(s)
    return s


def get_langfuse():
    global _client, _client_failed
    settings = get_settings()
    if not settings.langfuse_enabled or _client_failed:
        return None
    if _client is None:
        try:
            from langfuse import Langfuse

            _client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
                environment=settings.app_env,
                timeout=5,
            )
        except Exception as exc:  # pragma: no cover - depends on network
            log.warning("Langfuse disabled: %s", exc)
            _client_failed = True
            return None
    return _client


class _NoopObservation:
    id = None
    trace_id = None

    def update(self, **_: Any) -> "_NoopObservation":
        return self

    def score(self, **_: Any) -> None:
        return None

    def score_trace(self, **_: Any) -> None:
        return None


@contextmanager
def observation(name: str, as_type: str = "span", **kwargs: Any) -> Iterator[Any]:
    """Nested observation (span/agent/tool/retriever/embedding/generation/evaluator/guardrail)."""
    lf = get_langfuse()
    if lf is None:
        yield _NoopObservation()
        return
    try:
        cm = lf.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        obs = cm.__enter__()
    except Exception as exc:  # pragma: no cover
        log.warning("Langfuse observation failed: %s", exc)
        yield _NoopObservation()
        return
    try:
        yield obs
    except BaseException as exc:
        try:
            obs.update(level="ERROR", status_message=str(exc)[:500])
        except Exception:
            pass
        cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        cm.__exit__(None, None, None)


@contextmanager
def trace_request(
    name: str, *, request_id: str, user_id: str | None, session_id: str | None,
    input: Any = None, tags: list[str] | None = None,
) -> Iterator[Any]:
    """Root span for one API request. Session = thread_id, so one thread groups all its traces."""
    lf = get_langfuse()
    if lf is None:
        yield _NoopObservation()
        return
    from langfuse import propagate_attributes

    # A fresh trace_id makes this a NEW trace even if called inside another span (e.g. the eval run).
    with lf.start_as_current_observation(
        trace_context={"trace_id": lf.create_trace_id()},
        name=name, as_type="agent", input=input, metadata={"request_id": request_id}
    ) as root:
        with propagate_attributes(
            user_id=user_id, session_id=session_id, tags=tags or ["supportflow"],
            metadata={"request_id": request_id}, trace_name=name,
        ):
            yield root


def current_trace_id() -> str | None:
    lf = get_langfuse()
    if lf is None:
        return None
    try:
        return lf.get_current_trace_id()
    except Exception:
        return None


def trace_url(trace_id: str | None) -> str | None:
    lf = get_langfuse()
    if lf is None or not trace_id:
        return None
    try:
        return lf.get_trace_url(trace_id=trace_id)
    except Exception:
        return None


def langchain_callbacks() -> list:
    if get_langfuse() is None:
        return []
    try:
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]
    except Exception as exc:  # pragma: no cover
        log.warning("Langfuse LangChain handler unavailable: %s", exc)
        return []


def score_trace(trace_id: str | None, name: str, value: float | str, comment: str | None = None,
                data_type: str | None = None) -> None:
    lf = get_langfuse()
    if lf is None or not trace_id:
        return
    try:
        lf.create_score(trace_id=trace_id, name=name, value=value, comment=comment, data_type=data_type)
    except Exception as exc:  # pragma: no cover
        log.warning("Langfuse score failed: %s", exc)


def flush() -> None:
    lf = get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception:  # pragma: no cover
            pass


def health() -> str:
    lf = get_langfuse()
    if lf is None:
        return "disabled"
    try:
        return "ok" if lf.auth_check() else "auth_failed"
    except Exception as exc:  # pragma: no cover
        return f"error: {type(exc).__name__}"
