"""Detect and redact secrets: card numbers, security codes, one-time codes, passwords."""
from __future__ import annotations

import re
from typing import Any

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_CVV = re.compile(r"\b(cvv|cvc|cvv2|security code|card code)\b\s*(?:is|:|=|#)?\s*\d{3,4}\b", re.I)
_OTP = re.compile(r"\b(otp|one[- ]time (?:pass)?code|verification code|2fa code|auth(?:entication)? code|code)\b"
                  r"\s*(?:is|:|=|#)?\s*\d{4,8}\b", re.I)
_PASSWORD = re.compile(r"\b(password|passcode|pwd)\b\s*(?:is|:|=)\s*\S+", re.I)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def find_secrets(text: str) -> list[str]:
    kinds = []
    for m in _CARD.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            kinds.append("card_number")
            break
    if _CVV.search(text or ""):
        kinds.append("card_security_code")
    if _OTP.search(text or ""):
        kinds.append("one_time_code")
    if _PASSWORD.search(text or ""):
        kinds.append("password")
    return kinds


def redact(text: str) -> str:
    if not text:
        return text

    def _card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        return "[REDACTED_CARD]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group()

    text = _CARD.sub(_card, text)
    text = _CVV.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
    text = _OTP.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
    text = _PASSWORD.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
    return text


def redact_obj(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(exclude_none=True)
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
