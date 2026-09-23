"""The six controlled tools from the brief (+ ticket lookup). All scope checks happen HERE,
in the tool layer, so no prompt or model output can bypass them."""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Account, Message, Order, Thread, Ticket, User, utcnow
from app.db.session import session_scope
from app.rag import retriever
from app.rag.schemas import SearchFilters
from app.tools.base import ToolDenied, ToolNotFound, ToolResult, run_tool
from app.tools.redaction import redact, redact_obj

MAX_SEARCH_RESULTS = 8


class AuthContext(BaseModel):
    """Identity of the caller, resolved server-side from user_id (never from the chat text)."""
    user_id: str
    account_id: str | None = None
    workspace_id: str | None = None
    email: str | None = None
    verified: bool = False
    plan: str | None = None


def resolve_auth(user_id: str) -> AuthContext | None:
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return None
        acc = s.get(Account, user.account_id) if user.account_id else None
        return AuthContext(
            user_id=user.user_id, account_id=acc.account_id if acc else None,
            workspace_id=acc.workspace_id if acc else None, email=acc.email if acc else None,
            verified=bool(acc.verified) if acc else False, plan=acc.plan if acc else None,
        )


def _require_verified_account(auth: AuthContext, claimed_account_id: str | None) -> str:
    if not auth.account_id:
        raise ToolDenied("NO_ACCOUNT", "No CloudBox account is linked to this user.")
    if claimed_account_id and claimed_account_id != auth.account_id:
        raise ToolDenied("SCOPE_DENIED", "The account ID in the request does not match the signed-in account.")
    if not auth.verified:
        raise ToolDenied("UNVERIFIED", "The account has not passed verification, so private details cannot be shown.")
    return auth.account_id


# ---------------- search_knowledge_base ----------------
def search_knowledge_base(query: str, filters: SearchFilters | None = None, top_k: int = 5) -> ToolResult:
    top_k = max(1, min(top_k, MAX_SEARCH_RESULTS))
    return run_tool(
        "search_knowledge_base",
        lambda query, filters, top_k: retriever.search(query, filters, top_k),
        {"query": query, "filters": filters, "top_k": top_k},
        summarize=lambda r: f"{len(r.chunks)} chunks from {sorted({c.doc_id for c in r.chunks})}"
        + (" (conflict detected)" if r.conflicts else ""),
        timeout_s=get_settings().search_timeout_s,
    )


# ---------------- get_account ----------------
def _get_account(auth: AuthContext, account_id: str | None = None) -> dict:
    acc_id = _require_verified_account(auth, account_id)
    with session_scope() as s:
        acc = s.get(Account, acc_id)
        if acc is None:
            raise ToolNotFound("NOT_FOUND", "Account not found.")
        return {"account_id": acc.account_id, "plan": acc.plan, "workspace_id": acc.workspace_id,
                "customer_name": acc.customer_name, "verified": acc.verified}


def get_account(auth: AuthContext, account_id: str | None = None) -> ToolResult:
    return run_tool("get_account", lambda **kw: _get_account(auth, **kw), {"account_id": account_id},
                    summarize=lambda d: f"Account {d['account_id']} on {d['plan']} plan")


# ---------------- get_order ----------------
def _get_order(auth: AuthContext, order_id: str | None = None, invoice_id: str | None = None,
               account_id: str | None = None) -> dict:
    if not order_id and not invoice_id:
        raise ToolDenied("INVALID_ARGS", "An order ID or invoice ID is required.")
    if not auth.account_id:
        raise ToolDenied("NO_ACCOUNT", "No CloudBox account is linked to this user.")
    if account_id and account_id != auth.account_id:
        raise ToolDenied("SCOPE_DENIED", "The account ID in the request does not match the signed-in account.")
    with session_scope() as s:
        stmt = select(Order).where(Order.order_id == order_id) if order_id else select(Order).where(Order.invoice_id == invoice_id)
        order = s.execute(stmt).scalar_one_or_none()
        if order is None:
            raise ToolNotFound("NOT_FOUND", f"No record found for {order_id or invoice_id}.")
        # Ownership is checked before verification, and both failures are denials that expose nothing.
        if order.account_id != auth.account_id:
            raise ToolDenied("SCOPE_DENIED", "That order is not linked to the signed-in account.")
        _require_verified_account(auth, account_id)
        return {"order_id": order.order_id, "invoice_id": order.invoice_id, "plan": order.plan,
                "status": order.status, "amount_usd": order.amount_usd, "charged_at": order.charged_at,
                "account_id": order.account_id}


def get_order(auth: AuthContext, order_id: str | None = None, invoice_id: str | None = None,
              account_id: str | None = None) -> ToolResult:
    return run_tool(
        "get_order", lambda **kw: _get_order(auth, **kw),
        {"order_id": order_id, "invoice_id": invoice_id, "account_id": account_id or auth.account_id},
        summarize=lambda d: f"{d['order_id']} / {d['invoice_id']}: {d['amount_usd']} USD, {d['status']}",
    )


# ---------------- get_ticket (status lookup) ----------------
def _get_ticket(auth: AuthContext, ticket_id: str) -> dict:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            raise ToolNotFound("NOT_FOUND", f"Ticket {ticket_id} not found.")
        if t.account_id != auth.account_id and t.user_id != auth.user_id:
            raise ToolDenied("SCOPE_DENIED", "That ticket is not linked to the signed-in account.")
        return {"ticket_id": t.ticket_id, "category": t.category, "status": t.status, "summary": t.summary}


def get_ticket(auth: AuthContext, ticket_id: str) -> ToolResult:
    return run_tool("get_ticket", lambda **kw: _get_ticket(auth, **kw), {"ticket_id": ticket_id},
                    summarize=lambda d: f"{d['ticket_id']} is {d['status']}")


# ---------------- get_service_status ----------------
_scenario_override: str | None = None


def set_status_scenario(name: str | None) -> None:
    global _scenario_override
    _scenario_override = name


def current_status_scenario() -> str:
    return _scenario_override or os.getenv("STATUS_SCENARIO", "operational")


def _get_service_status(component: str | None = None) -> dict:
    feed = json.loads((get_settings().data_dir / "service_status.json").read_text(encoding="utf-8"))
    scenario = current_status_scenario()
    if scenario not in feed["scenarios"]:
        raise RuntimeError(f"Unknown status scenario {scenario}")
    data = dict(feed["scenarios"][scenario])
    data["checked_at"] = utcnow().isoformat(timespec="seconds")
    if component:
        data["components"] = [c for c in data["components"] if component.lower() in c["name"].lower()] or data["components"]
    return data


def get_service_status(component: str | None = None) -> ToolResult:
    return run_tool(
        "get_service_status", _get_service_status, {"component": component},
        summarize=lambda d: f"overall={d['overall']}, active incidents={len(d['active_incidents'])}",
    )


# ---------------- create_ticket ----------------
class TicketInput(BaseModel):
    category: str
    summary: str
    priority: str = "normal"
    product_area: str | None = None
    steps_tried: list[str] = []
    error_message: str | None = None
    related_ids: list[str] = []
    thread_id: str | None = None
    details: dict[str, Any] = {}


def _next_ticket_id(s) -> str:
    ids = [int(t.split("-")[1]) for t in s.execute(select(Ticket.ticket_id)).scalars() if t.startswith("SUP-")]
    return f"SUP-{max(ids + [3003]) + 1}"


def _create_ticket(auth: AuthContext, ticket: TicketInput) -> dict:
    # Secrets never reach the database: every free-text field is redacted first.
    clean = TicketInput(**redact_obj(ticket.model_dump()))
    with session_scope() as s:
        tid = _next_ticket_id(s)
        s.add(Ticket(
            ticket_id=tid, account_id=auth.account_id, user_id=auth.user_id, workspace_id=auth.workspace_id,
            thread_id=clean.thread_id, category=clean.category, priority=clean.priority, status="open",
            summary=clean.summary[:1000], product_area=clean.product_area, steps_tried=clean.steps_tried,
            error_message=clean.error_message, related_ids=clean.related_ids,
            details={**clean.details, "created_by": "supportflow", "timestamp": utcnow().isoformat()},
        ))
    return {"ticket_id": tid, "category": clean.category, "priority": clean.priority, "status": "open"}


def create_ticket(auth: AuthContext, ticket: TicketInput) -> ToolResult:
    return run_tool("create_ticket", lambda **kw: _create_ticket(auth, **kw), {"ticket": ticket},
                    summarize=lambda d: f"Created {d['ticket_id']} ({d['category']}, {d['priority']})")


# ---------------- get_thread_summary ----------------
def _get_thread_summary(auth: AuthContext, thread_id: str, max_turns: int = 6) -> dict:
    with session_scope() as s:
        thread = s.get(Thread, thread_id)
        if thread is None:
            raise ToolNotFound("NOT_FOUND", "Thread not found.")
        if thread.user_id != auth.user_id:
            raise ToolDenied("THREAD_DENIED", "That thread belongs to another user.")
        msgs = s.execute(
            select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.desc()).limit(max_turns * 2)
        ).scalars().all()
        msgs = list(reversed(msgs))
        turns = [{"role": m.role, "content": redact(m.content)[:600], "route": m.route} for m in msgs]
        user_msgs = [m.content for m in msgs if m.role == "user"]
        summary = thread.summary or ("; ".join(u[:120] for u in user_msgs[-3:]) if user_msgs else "")
        return {"thread_id": thread_id, "summary": summary, "turns": turns}


def get_thread_summary(auth: AuthContext, thread_id: str, max_turns: int = 6) -> ToolResult:
    return run_tool("get_thread_summary", lambda **kw: _get_thread_summary(auth, **kw),
                    {"thread_id": thread_id, "max_turns": max_turns},
                    summarize=lambda d: f"{len(d['turns'])} previous messages")
