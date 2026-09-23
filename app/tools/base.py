"""Shared tool runtime: structured results, timeouts, bounded retries, fault injection, tracing.

Tools never raise into the graph. Every call returns a ToolResult and appends a ToolEvent.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.observability.tracing import current_stats, observation
from app.tools.redaction import redact_obj

ToolStatus = Literal["success", "error", "denied"]


class ToolEvent(BaseModel):
    tool: str
    status: ToolStatus
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error_code: str | None = None
    latency_ms: int = 0
    attempts: int = 1


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str = ""
    event: ToolEvent


class ToolDenied(Exception):
    """Raised inside a tool for scope/verification failures (not retried)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ToolNotFound(ToolDenied):
    pass


class ToolTimeout(Exception):
    pass


# Per-request fault injection, e.g. {"get_order": "timeout"}. Only set in development/eval runs.
_faults: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("sf_faults", default={})
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


def set_fault_injection(faults: dict[str, str] | None) -> None:
    _faults.set(dict(faults or {}))


def _maybe_inject(name: str) -> None:
    mode = _faults.get().get(name)
    if mode == "timeout":
        time.sleep(get_settings().tool_timeout_s + 0.5)
    elif mode == "error":
        raise RuntimeError(f"Injected failure in {name}")


def run_tool(name: str, fn: Callable[..., Any], args: dict[str, Any], summarize: Callable[[Any], str] | None = None,
             timeout_s: float | None = None) -> ToolResult:
    settings = get_settings()
    timeout = timeout_s or settings.tool_timeout_s
    max_attempts = 1 + settings.tool_max_retries
    safe_args = redact_obj(args)
    start = time.perf_counter()
    attempts = 0
    current_stats().tool_calls += 1
    with observation(f"tool:{name}", as_type="tool", input=safe_args) as obs:
        last_code, last_msg = "TOOL_ERROR", "Unknown error"
        while attempts < max_attempts:
            attempts += 1
            ctx = contextvars.copy_context()

            def _call():
                _maybe_inject(name)
                return fn(**args)

            future = _pool.submit(ctx.run, _call)
            try:
                data = future.result(timeout=timeout)
                ms = int((time.perf_counter() - start) * 1000)
                summary = summarize(data) if summarize else "ok"
                event = ToolEvent(tool=name, status="success", args=safe_args, summary=summary,
                                  latency_ms=ms, attempts=attempts)
                obs.update(output=redact_obj(data))
                current_stats().add_time("tools", ms)
                return ToolResult(ok=True, data=data, message=summary, event=event)
            except ToolDenied as exc:  # scope failures are final, never retried
                last_code, last_msg = exc.code, exc.message
                break
            except concurrent.futures.TimeoutError:
                last_code, last_msg = "TIMEOUT", f"{name} did not respond within {timeout:.0f}s"
            except Exception as exc:  # noqa: BLE001 - convert everything to a structured error
                last_code, last_msg = "TOOL_ERROR", f"{name} failed: {type(exc).__name__}"
        ms = int((time.perf_counter() - start) * 1000)
        status: ToolStatus = "denied" if last_code in {"SCOPE_DENIED", "UNVERIFIED", "NO_ACCOUNT", "THREAD_DENIED"} else "error"
        event = ToolEvent(tool=name, status=status, args=safe_args, summary=last_msg, error_code=last_code,
                          latency_ms=ms, attempts=attempts)
        obs.update(level="WARNING" if status == "denied" else "ERROR", status_message=f"{last_code}: {last_msg}")
        current_stats().add_time("tools", ms)
        return ToolResult(ok=False, error_code=last_code, message=last_msg, event=event)
