"""FastAPI entrypoint: `uvicorn app.main:app --reload`. Interactive docs at /docs."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.db.models import RequestLog
from app.db.seed import seed_fixtures
from app.db.session import init_db, session_scope
from app.observability import tracing
from app.rag import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("supportflow")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    init_db()
    log.info("fixtures: %s", seed_fixtures())
    if settings.auto_ingest:
        try:
            if store.count_points() == 0:
                from app.rag.ingest import ingest_directory

                log.info("vector collection empty, ingesting rag_materials: %s", ingest_directory())
            else:
                log.info("vector collection already has %d chunks (kept across restarts)", store.count_points())
        except Exception as exc:  # the API still starts; /health reports the vector DB as degraded
            log.error("auto-ingest failed: %s", exc)
    yield
    tracing.flush()


app = FastAPI(
    title="SupportFlow API",
    version=__version__,
    description="CloudBox customer-support agent: LangGraph multi-agent workflow with RAG, controlled tools, "
                "DeepEval evaluation and Langfuse monitoring. Every response carries an X-Request-ID header that "
                "matches the request_id stored with the Langfuse trace.",
    lifespan=lifespan,
)

_s = get_settings()
app.add_middleware(
    CORSMiddleware, allow_origins=_s.frontend_origins, allow_origin_regex=_s.frontend_origin_regex,
    allow_credentials=False, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"
    request.state.request_id = rid
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # last-resort handler: structured error, never a raw stack trace
        log.exception("unhandled error request_id=%s", rid)
        _log_failure(rid, request.url.path, f"{type(exc).__name__}", int((time.perf_counter() - start) * 1000))
        response = JSONResponse(status_code=500, content={
            "error": "internal_error", "detail": "Unexpected server error.", "request_id": rid})
    response.headers["X-Request-ID"] = rid
    return response


def _log_failure(rid: str, path: str, error: str, ms: int) -> None:
    try:
        with session_scope() as s:
            s.merge(RequestLog(request_id=rid, endpoint=path, latency_ms=ms, status_code=500, error=error))
    except Exception:
        pass


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={
        "error": {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
                  422: "invalid_input", 503: "dependency_unavailable"}.get(exc.status_code, "error"),
        "detail": exc.detail, "request_id": getattr(request.state, "request_id", None)})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "error": "invalid_input",
        "detail": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
        "request_id": getattr(request.state, "request_id", None)})


app.include_router(router)
