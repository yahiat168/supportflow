"""Runs one chat turn: auth + thread scope -> redaction -> graph -> persistence -> monitoring."""
from __future__ import annotations

import logging
import time

from sqlalchemy import select

from app.api.schemas import ChatResponse, Citation, EscalationOut, ToolEventOut
from app.config import get_settings
from app.db.models import Message, RequestLog, Thread, utcnow
from app.db.session import session_scope
from app.graph.orchestrator import get_graph
from app.observability import tracing
from app.tools.base import set_fault_injection
from app.tools.redaction import find_secrets, redact
from app.tools.support_tools import AuthContext, get_thread_summary, resolve_auth

log = logging.getLogger("supportflow.chat")


class UnknownUser(Exception):
    pass


class ThreadAccessDenied(Exception):
    pass


class ThreadNotFound(Exception):
    pass


def create_thread(user_id: str, title: str | None = None) -> Thread:
    if resolve_auth(user_id) is None:
        raise UnknownUser(user_id)
    with session_scope() as s:
        t = Thread(user_id=user_id, title=(title or "New conversation")[:200])
        s.add(t)
        s.flush()
        return t


def _load_thread(user_id: str, thread_id: str | None, first_message: str) -> str:
    if thread_id is None:
        return create_thread(user_id, first_message[:60]).thread_id
    with session_scope() as s:
        t = s.get(Thread, thread_id)
        if t is None:
            raise ThreadNotFound(thread_id)
        if t.user_id != user_id:
            raise ThreadAccessDenied(thread_id)
        return t.thread_id


def run_chat(user_id: str, thread_id: str | None, message: str, request_id: str,
             fault_injection: dict[str, str] | None = None, capture: dict | None = None) -> ChatResponse:
    settings = get_settings()
    start = time.perf_counter()
    auth: AuthContext | None = resolve_auth(user_id)
    if auth is None:
        raise UnknownUser(user_id)
    secrets = find_secrets(message)
    clean = redact(message)  # secrets never reach the model, the trace, or the database
    thread_id = _load_thread(user_id, thread_id, clean)
    stats = tracing.new_stats(request_id)
    set_fault_injection(fault_injection)

    error: str | None = None
    result: dict = {}
    with tracing.trace_request("supportflow.chat", request_id=request_id, user_id=user_id, session_id=thread_id,
                               input={"message": clean}, tags=["chat", settings.llm_provider]) as root:
        trace_id = tracing.current_trace_id()
        summary = get_thread_summary(auth, thread_id, max_turns=settings.history_turns)
        history = summary.data["turns"] if summary.ok else []
        state = {
            "request_id": request_id, "user_id": user_id, "thread_id": thread_id, "request": clean,
            "history": history, "auth": auth.model_dump(), "secrets_detected": secrets,
            "fault_injection": fault_injection or {}, "trace_id": trace_id,
            "tool_events": [summary.event.model_dump()], "subagent_results": [], "trajectory": [],
        }
        try:
            result = get_graph().invoke(state, config={"recursion_limit": settings.graph_recursion_limit})
        except Exception as exc:  # the API must answer even if the graph fails
            log.exception("graph failure request_id=%s", request_id)
            error = f"{type(exc).__name__}: {exc}"[:500]
            result = {**state, "route": "error", "final_answer": (
                "Sorry, something went wrong while handling your request, and I couldn't complete it. "
                f"Please try again. If this keeps happening, share this reference with support: {request_id}."),
                "citations": [], "escalation": {"needs_escalation": False}, "no_evidence": True}
        root.update(output={"answer": result.get("final_answer", "")[:1000], "route": result.get("route"),
                            "needs_escalation": (result.get("escalation") or {}).get("needs_escalation", False)},
                    metadata={"trajectory": result.get("trajectory"), "timings_ms": stats.timings_ms,
                              "model_calls": stats.model_calls, "tokens": stats.total_tokens},
                    level="ERROR" if error else "DEFAULT")
    latency_ms = int((time.perf_counter() - start) * 1000)
    esc = result.get("escalation") or {}
    tool_events = result.get("tool_events", [])

    with session_scope() as s:
        s.add(Message(thread_id=thread_id, role="user", content=clean, request_id=request_id, trace_id=trace_id))
        s.flush()
        amsg = Message(
            thread_id=thread_id, role="assistant", content=result.get("final_answer", ""), route=result.get("route"),
            citations=result.get("citations", []), tool_events=tool_events, trajectory=result.get("trajectory", []),
            needs_escalation=bool(esc.get("needs_escalation")), ticket_id=esc.get("ticket_id"),
            request_id=request_id, trace_id=trace_id, created_at=utcnow(),
        )
        s.add(amsg)
        t = s.get(Thread, thread_id)
        t.updated_at = utcnow()
        users = [m.content for m in s.execute(select(Message).where(Message.thread_id == thread_id,
                                                                    Message.role == "user")).scalars()]
        t.summary = " | ".join(u[:100] for u in users[-3:])
        if t.title == "New conversation":
            t.title = clean[:60]
        s.add(RequestLog(request_id=request_id, user_id=user_id, thread_id=thread_id, endpoint="/chat",
                         route=result.get("route"), latency_ms=latency_ms, status_code=500 if error else 200,
                         error=error, needs_escalation=bool(esc.get("needs_escalation")),
                         model_calls=stats.model_calls, tokens=stats.total_tokens, trace_id=trace_id))
        s.flush()
        message_id = amsg.message_id

    if capture is not None:
        capture.update(state=result, model_calls=stats.model_calls, tokens=stats.total_tokens,
                       timings_ms=dict(stats.timings_ms), trace_id=trace_id)
    tracing.score_trace(trace_id, "critic_approved", 1.0 if (result.get("critic") or {}).get("approved") else 0.0,
                        comment="; ".join((result.get("critic") or {}).get("issues", []))[:500] or None)
    tracing.score_trace(trace_id, "escalated", 1.0 if esc.get("needs_escalation") else 0.0)
    tracing.score_trace(trace_id, "no_evidence", 1.0 if result.get("no_evidence") else 0.0)
    # No per-request flush: the Langfuse SDK exports in a background thread (flushed on shutdown).

    return ChatResponse(
        thread_id=thread_id, answer=result.get("final_answer", ""),
        citations=[Citation(**c) for c in result.get("citations", [])],
        tool_events=[ToolEventOut(**e) for e in tool_events],
        needs_escalation=bool(esc.get("needs_escalation")), message_id=message_id, route=result.get("route", "error"),
        escalation=EscalationOut(needs_escalation=bool(esc.get("needs_escalation")), category=esc.get("category"),
                                 ticket_id=esc.get("ticket_id"), priority=esc.get("priority")),
        ticket_id=esc.get("ticket_id"), no_evidence=bool(result.get("no_evidence")),
        missing_information=result.get("missing_information", []) or [],
        trajectory=result.get("trajectory", []), request_id=request_id, trace_id=trace_id,
        trace_url=tracing.trace_url(trace_id), latency_ms=latency_ms,
    )
