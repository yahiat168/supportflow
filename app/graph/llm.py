"""Model access with call budgets, timeouts, retries, token accounting and Langfuse callbacks."""
from __future__ import annotations

import time
from functools import lru_cache
from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings
from app.observability.tracing import current_stats, langchain_callbacks

T = TypeVar("T", bound=BaseModel)


class LLMBudgetExceeded(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


@lru_cache
def chat_model():
    from langchain_openai import ChatOpenAI

    s = get_settings()
    if not s.model_api_key:
        raise LLMUnavailable("MODEL_API_KEY is not set (or use LLM_PROVIDER=offline).")
    return ChatOpenAI(
        model=s.model_name, api_key=s.model_api_key, base_url=s.model_base_url,
        timeout=s.model_timeout_s, max_retries=s.model_max_retries, temperature=0,
    )


def _check_budget() -> None:
    stats = current_stats()
    if stats.model_calls >= get_settings().max_model_calls:
        raise LLMBudgetExceeded(f"Model call budget of {get_settings().max_model_calls} reached")
    stats.model_calls += 1


def _record_usage(msg) -> None:
    usage = getattr(msg, "usage_metadata", None) or {}
    stats = current_stats()
    stats.input_tokens += int(usage.get("input_tokens", 0))
    stats.output_tokens += int(usage.get("output_tokens", 0))


def structured(schema: type[T], system: str, user: str, name: str) -> T:
    _check_budget()
    start = time.perf_counter()
    try:
        from app.ratelimit import with_backoff

        runnable = chat_model().with_structured_output(
            schema, method=get_settings().structured_output_method, include_raw=True)
        out = with_backoff(lambda: runnable.invoke(
            [("system", system), ("user", user)],
            config={"callbacks": langchain_callbacks(), "run_name": name, "tags": [name]},
        ), waits=get_settings().chat_rate_limit_waits, what=name)
    except (LLMBudgetExceeded, LLMUnavailable):
        raise
    except Exception as exc:
        raise LLMUnavailable(f"{name} model call failed: {type(exc).__name__}") from exc
    finally:
        current_stats().add_time("model", (time.perf_counter() - start) * 1000)
    _record_usage(out.get("raw"))
    if out.get("parsed") is None:
        raise LLMUnavailable(f"{name}: could not parse structured output")
    return out["parsed"]
