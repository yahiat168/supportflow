from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.config import get_settings


def request_id(request: Request) -> str:
    return request.state.request_id


def require_dev_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Protected development routes (/evals/run, /dev/*): never in production, token always required."""
    s = get_settings()
    if s.is_production:
        raise HTTPException(status_code=403, detail="Development route disabled in production.")
    if not s.admin_token:
        raise HTTPException(status_code=403, detail="Set EVALS_ADMIN_TOKEN to enable this route.")
    if x_admin_token != s.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token header.")


def require_admin_if_configured(x_admin_token: str | None = Header(default=None)) -> None:
    """Document ingestion: open in local development, token-protected once a token is configured."""
    s = get_settings()
    if s.admin_token and x_admin_token != s.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token header.")
    if s.is_production and not s.admin_token:
        raise HTTPException(status_code=403, detail="Document ingestion requires EVALS_ADMIN_TOKEN in production.")


def parse_fault_header(x_fault_inject: str | None = Header(default=None)) -> dict[str, str]:
    """Development-only fault injection, e.g. 'get_order:timeout,get_service_status:error'."""
    if not x_fault_inject or get_settings().is_production:
        return {}
    out = {}
    for part in x_fault_inject.split(","):
        if ":" in part:
            tool, mode = part.split(":", 1)
            if mode.strip() in {"timeout", "error"}:
                out[tool.strip()] = mode.strip()
    return out
