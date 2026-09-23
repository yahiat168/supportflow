"""All HTTP endpoints. Business logic lives in services/tools; routes stay thin."""
from __future__ import annotations

import statistics
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select, text

from app import __version__
from app.api.deps import parse_fault_header, request_id, require_admin_if_configured, require_dev_admin
from app.api.schemas import (ChatRequest, ChatResponse, DocumentIn, DocumentOut, EvalRunIn, EvalRunOut, FeedbackIn,
                             FeedbackOut, HealthOut, MessageOut, MetricsSummary, ThreadCreate, ThreadDetail, ThreadOut,
                             TicketIn, TicketOut, ToolEventOut, UserOut)
from app.config import get_settings
from app.db.models import Account, Document, EvalRun, Feedback, Message, RequestLog, Thread, User, utcnow
from app.db.session import session_scope
from app.observability import tracing
from app.rag import store
from app.rag.ingest import ingest_text
from app.rag.loader import DocumentFormatError
from app.services import chat_service
from app.tools.support_tools import (TicketInput, create_ticket, current_status_scenario, resolve_auth,
                                     set_status_scenario)

router = APIRouter()


# ---------------- health ----------------
@router.get("/health", response_model=HealthOut, tags=["system"])
def health() -> HealthOut:
    s = get_settings()
    deps: dict[str, str] = {}
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        deps["database"] = "ok"
    except Exception as exc:
        deps["database"] = f"error: {type(exc).__name__}"
    deps["vector_db"] = store.health()
    deps["langfuse"] = tracing.health()
    deps["model"] = "offline" if s.is_offline else ("configured" if s.model_api_key else "missing MODEL_API_KEY")
    try:
        chunks = store.count_points()
    except Exception:
        chunks = 0
    try:
        with session_scope() as db:
            docs = db.scalar(select(func.count()).select_from(Document)) or 0
    except Exception:
        docs = 0
    ok = deps["database"] == "ok" and deps["vector_db"] == "ok" and deps["model"] != "missing MODEL_API_KEY"
    return HealthOut(status="ok" if ok else "degraded", version=__version__, environment=s.app_env,
                     llm_provider=s.llm_provider, dependencies=deps, indexed_documents=docs, indexed_chunks=chunks)


# ---------------- chat ----------------
@router.post("/chat", response_model=ChatResponse, tags=["chat"],
             responses={403: {"description": "Thread belongs to another user"}, 404: {"description": "Unknown user or thread"}})
def chat(body: ChatRequest, rid: str = Depends(request_id), faults: dict = Depends(parse_fault_header)) -> ChatResponse:
    try:
        return chat_service.run_chat(body.user_id, body.thread_id, body.message, rid, faults)
    except chat_service.UnknownUser:
        raise HTTPException(404, detail="Unknown user_id.")
    except chat_service.ThreadNotFound:
        raise HTTPException(404, detail="Thread not found.")
    except chat_service.ThreadAccessDenied:
        raise HTTPException(403, detail="This thread belongs to another user.")


# ---------------- users (demo identity switcher) ----------------
@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users() -> list[UserOut]:
    with session_scope() as s:
        out = []
        for u in s.execute(select(User).order_by(User.user_id)).scalars():
            acc = s.get(Account, u.account_id) if u.account_id else None
            out.append(UserOut(user_id=u.user_id, display_name=u.display_name, account_id=u.account_id,
                               plan=acc.plan if acc else None, verified=bool(acc and acc.verified)))
        return out


# ---------------- threads ----------------
def _thread_out(t: Thread, count: int) -> dict:
    return dict(thread_id=t.thread_id, user_id=t.user_id, title=t.title, summary=t.summary,
                created_at=t.created_at, updated_at=t.updated_at, message_count=count)


@router.post("/threads", response_model=ThreadOut, status_code=201, tags=["threads"])
def create_thread(body: ThreadCreate) -> ThreadOut:
    try:
        t = chat_service.create_thread(body.user_id, body.title)
    except chat_service.UnknownUser:
        raise HTTPException(404, detail="Unknown user_id.")
    return ThreadOut(**_thread_out(t, 0))


@router.get("/threads", response_model=list[ThreadOut], tags=["threads"])
def list_threads(user_id: str = Query(...), limit: int = Query(50, le=200)) -> list[ThreadOut]:
    with session_scope() as s:
        rows = s.execute(select(Thread).where(Thread.user_id == user_id).order_by(Thread.updated_at.desc()).limit(limit)).scalars().all()
        counts = dict(s.execute(select(Message.thread_id, func.count()).where(
            Message.thread_id.in_([t.thread_id for t in rows])).group_by(Message.thread_id)).all())
        return [ThreadOut(**_thread_out(t, counts.get(t.thread_id, 0))) for t in rows]


@router.get("/threads/{thread_id}", response_model=ThreadDetail, tags=["threads"])
def get_thread(thread_id: str, user_id: str = Query(..., description="Caller; must own the thread")) -> ThreadDetail:
    with session_scope() as s:
        t = s.get(Thread, thread_id)
        if t is None:
            raise HTTPException(404, detail="Thread not found.")
        if t.user_id != user_id:
            raise HTTPException(403, detail="This thread belongs to another user.")
        msgs = s.execute(select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at)).scalars().all()
        return ThreadDetail(**_thread_out(t, len(msgs)), messages=[MessageOut(
            message_id=m.message_id, role=m.role, content=m.content, route=m.route, citations=m.citations or [],
            tool_events=m.tool_events or [], needs_escalation=m.needs_escalation, ticket_id=m.ticket_id,
            trace_id=m.trace_id, created_at=m.created_at) for m in msgs])


# ---------------- documents ----------------
def _doc_out(d: Document) -> DocumentOut:
    return DocumentOut(doc_id=d.doc_id, title=d.title, product=d.product, version=d.version, source_type=d.source_type,
                       trust_level=d.trust_level, filename=d.filename, chunk_count=d.chunk_count, indexed_at=d.indexed_at)


def _ingest(raw: str, filename: str) -> DocumentOut:
    try:
        meta, _ = ingest_text(raw, filename)
    except DocumentFormatError as exc:
        raise HTTPException(422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(503, detail=f"Vector database or embedding service unavailable: {type(exc).__name__}")
    with session_scope() as s:
        return _doc_out(s.get(Document, meta.doc_id))


@router.post("/documents", response_model=DocumentOut, status_code=201, tags=["documents"],
             dependencies=[Depends(require_admin_if_configured)])
def ingest_document(body: DocumentIn) -> DocumentOut:
    """Ingest one Markdown document that starts with the metadata block (doc_id, title, product, version,
    source_type, trust_level). Re-ingesting the same doc_id replaces its chunks."""
    return _ingest(body.content, body.filename)


@router.post("/documents/upload", response_model=DocumentOut, status_code=201, tags=["documents"],
             dependencies=[Depends(require_admin_if_configured)])
async def upload_document(file: UploadFile = File(...)) -> DocumentOut:
    if not (file.filename or "").endswith(".md"):
        raise HTTPException(422, detail="Only Markdown (.md) files are supported.")
    raw = (await file.read()).decode("utf-8", errors="replace")
    return _ingest(raw, Path(file.filename).name)


@router.get("/documents", response_model=list[DocumentOut], tags=["documents"])
def list_documents(source_type: str | None = None, trust_level: str | None = None) -> list[DocumentOut]:
    with session_scope() as s:
        stmt = select(Document).order_by(Document.doc_id)
        if source_type:
            stmt = stmt.where(Document.source_type == source_type)
        if trust_level:
            stmt = stmt.where(Document.trust_level == trust_level)
        return [_doc_out(d) for d in s.execute(stmt).scalars()]


# ---------------- feedback ----------------
@router.post("/feedback", response_model=FeedbackOut, status_code=201, tags=["feedback"])
def feedback(body: FeedbackIn) -> FeedbackOut:
    with session_scope() as s:
        msg = s.get(Message, body.message_id)
        if msg is None or msg.role != "assistant":
            raise HTTPException(404, detail="Assistant message not found.")
        if s.get(Thread, msg.thread_id).user_id != body.user_id:
            raise HTTPException(403, detail="You can only rate responses in your own threads.")
        fb = Feedback(message_id=body.message_id, user_id=body.user_id, helpful=body.helpful, comment=body.comment)
        s.add(fb)
        s.flush()
        trace_id, fid = msg.trace_id, fb.feedback_id
    tracing.score_trace(trace_id, "user_feedback", 1.0 if body.helpful else 0.0, comment=body.comment)
    return FeedbackOut(feedback_id=fid, message_id=body.message_id, helpful=body.helpful, trace_id=trace_id)


# ---------------- tickets ----------------
@router.post("/tickets", response_model=TicketOut, status_code=201, tags=["tickets"])
def post_ticket(body: TicketIn) -> TicketOut:
    auth = resolve_auth(body.user_id)
    if auth is None:
        raise HTTPException(404, detail="Unknown user_id.")
    if body.thread_id:
        with session_scope() as s:
            t = s.get(Thread, body.thread_id)
            if t is None or t.user_id != body.user_id:
                raise HTTPException(403, detail="Thread not found for this user.")
    res = create_ticket(auth, TicketInput(**body.model_dump(exclude={"user_id"})))
    if not res.ok:
        raise HTTPException(503, detail={"error_code": res.error_code, "message": res.message})
    return TicketOut(**res.data, tool_event=ToolEventOut(**res.event.model_dump()))


# ---------------- monitoring ----------------
@router.get("/metrics/summary", response_model=MetricsSummary, tags=["monitoring"])
def metrics_summary(hours: int = Query(24, ge=1, le=720), recent: int = Query(20, le=100)) -> MetricsSummary:
    s_ = get_settings()
    since = utcnow() - timedelta(hours=hours)
    with session_scope() as s:
        logs = s.execute(select(RequestLog).where(RequestLog.created_at >= since, RequestLog.endpoint == "/chat")
                         .order_by(RequestLog.created_at.desc())).scalars().all()
        fb = s.execute(select(Feedback.helpful).where(Feedback.created_at >= since)).scalars().all()
    n = len(logs)
    lat = sorted(l.latency_ms for l in logs)
    errors = sum(1 for l in logs if l.error or l.status_code >= 500)
    esc = sum(1 for l in logs if l.needs_escalation)
    routes: dict[str, int] = {}
    for l in logs:
        routes[l.route or "unknown"] = routes.get(l.route or "unknown", 0) + 1
    p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0
    err_rate, esc_rate = (errors / n if n else 0.0), (esc / n if n else 0.0)
    neg_rate = (sum(1 for h in fb if not h) / len(fb)) if fb else 0.0
    alerts = []
    if n >= 5 and err_rate > s_.alert_error_rate:
        alerts.append(f"Error rate {err_rate:.0%} above {s_.alert_error_rate:.0%}")
    if lat and p95 > s_.alert_latency_ms:
        alerts.append(f"p95 latency {p95} ms above {s_.alert_latency_ms} ms")
    if n >= 5 and esc_rate > s_.alert_escalation_rate:
        alerts.append(f"Escalation rate {esc_rate:.0%} above {s_.alert_escalation_rate:.0%} (possible incident)")
    if len(fb) >= 3 and neg_rate > s_.alert_negative_feedback_rate:
        alerts.append(f"Negative feedback {neg_rate:.0%} above {s_.alert_negative_feedback_rate:.0%}")
    repeated = [l.error.split(":")[0] for l in logs[:10] if l.error]
    if len(repeated) >= 3 and len(set(repeated)) == 1:
        alerts.append(f"Repeated error: {repeated[0]} x{len(repeated)} in the last 10 requests")
    return MetricsSummary(
        window=f"last {hours}h", total_requests=n, error_count=errors, error_rate=round(err_rate, 3),
        avg_latency_ms=round(statistics.mean(lat), 1) if lat else 0.0, p95_latency_ms=float(p95),
        escalation_rate=round(esc_rate, 3), negative_feedback_rate=round(neg_rate, 3), routes=routes,
        total_tokens=sum(l.tokens for l in logs), alerts=alerts,
        recent=[{"request_id": l.request_id, "user_id": l.user_id, "route": l.route, "latency_ms": l.latency_ms,
                 "status_code": l.status_code, "error": l.error, "needs_escalation": l.needs_escalation,
                 "tokens": l.tokens, "trace_id": l.trace_id, "created_at": l.created_at.isoformat()}
                for l in logs[:recent]],
    )


# ---------------- evals (protected development route) ----------------
@router.post("/evals/run", response_model=EvalRunOut, status_code=202, tags=["evals"],
             dependencies=[Depends(require_dev_admin)])
def run_evals(body: EvalRunIn, background: BackgroundTasks) -> EvalRunOut:
    from evals.runner import run_eval_job

    with session_scope() as s:
        run = EvalRun(status="running", summary={})
        s.add(run)
        s.flush()
        run_id = run.run_id
    background.add_task(run_eval_job, run_id, body.include_extra, body.case_ids, body.use_deepeval)
    return EvalRunOut(run_id=run_id, status="running")


@router.get("/evals/runs/{run_id}", response_model=EvalRunOut, tags=["evals"], dependencies=[Depends(require_dev_admin)])
def get_eval_run(run_id: str) -> EvalRunOut:
    with session_scope() as s:
        r = s.get(EvalRun, run_id)
        if r is None:
            raise HTTPException(404, detail="Eval run not found.")
        return EvalRunOut(run_id=r.run_id, status=r.status, summary=r.summary or {}, report_path=r.report_path)


# ---------------- dev helpers ----------------
@router.post("/dev/status-scenario", tags=["dev"], dependencies=[Depends(require_dev_admin)])
def set_scenario(scenario: str = Query(..., pattern="^(operational|previews_degraded|sync_outage)$")) -> dict:
    """Switch the mock live status feed (for the outage part of the demo)."""
    set_status_scenario(scenario)
    return {"scenario": current_status_scenario()}
