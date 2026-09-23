"""Patient retries for transient provider failures: rate limits (HTTP 429) on a shared proxy key,
dropped connections / timeouts on an unstable network, and temporary 5xx server errors."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from app.config import get_settings

T = TypeVar("T")
log = logging.getLogger("supportflow.ratelimit")


def is_rate_limit(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    text = str(exc)
    return "429" in text and ("RateLimit" in text or "RESOURCE_EXHAUSTED" in text or "Too Many" in text)


_TRANSIENT_NAMES = ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout",
                    "RemoteProtocolError", "ConnectionError", "TimeoutError", "InternalServerError")


def is_transient(exc: BaseException) -> bool:
    """429s plus network drops and 5xx; walks wrapped causes (e.g. tenacity RetryError around APIConnectionError)."""
    seen = 0
    while exc is not None and seen < 5:
        if is_rate_limit(exc) or getattr(exc, "status_code", None) in (500, 502, 503, 504):
            return True
        if type(exc).__name__ in _TRANSIENT_NAMES:
            return True
        text = str(exc)
        if any(n in text for n in _TRANSIENT_NAMES) or "Connection error" in text:
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def with_backoff(fn: Callable[[], T], waits: list[float] | None = None, what: str = "request") -> T:
    """Call fn(); on a transient failure wait (5, 10, 20, 40, 60 s by default) and try again."""
    waits = waits if waits is not None else get_settings().rate_limit_waits
    for attempt in range(len(waits) + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not is_transient(exc) or attempt == len(waits):
                raise
            kind = "rate-limited (429)" if is_rate_limit(exc) else f"temporary failure ({type(exc).__name__})"
            log.warning("%s %s; waiting %.0fs before retry %d/%d", what, kind, waits[attempt],
                        attempt + 1, len(waits))
            time.sleep(waits[attempt])
    raise RuntimeError("unreachable")
