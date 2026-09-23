"""Pydantic request/response models for every endpoint."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    doc_id: str
    title: str
    version: str
    source_type: str
    trust_level: str
    snippet: str
    score: float


class ToolEventOut(BaseModel):
    tool: str
    status: Literal["success", "error", "denied"]
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error_code: str | None = None
    latency_ms: int = 0
    attempts: int = 1


class EscalationOut(BaseModel):
    needs_escalation: bool = False
    category: str | None = None
    ticket_id: str | None = None
    priority: str | None = None


class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    citations: list[Citation]
    tool_events: list[ToolEventOut]
    needs_escalation: bool
    # Extensions used by the frontend, feedback and debugging:
    message_id: str
    route: str
    escalation: EscalationOut
    ticket_id: str | None = None
    no_evidence: bool = False
    missing_information: list[str] = Field(default_factory=list)
    trajectory: list[str] = Field(default_factory=list)
    request_id: str
    trace_id: str | None = None
    trace_url: str | None = None
    latency_ms: int = 0


class ThreadCreate(BaseModel):
    user_id: str
    title: str | None = None


class ThreadOut(BaseModel):
    thread_id: str
    user_id: str
    title: str
    summary: str = ""
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str
    route: str | None = None
    citations: list[dict] = Field(default_factory=list)
    tool_events: list[dict] = Field(default_factory=list)
    needs_escalation: bool = False
    ticket_id: str | None = None
    trace_id: str | None = None
    created_at: datetime


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


class DocumentIn(BaseModel):
    filename: str = Field(pattern=r"^[\w\-.]+\.md$")
    content: str = Field(min_length=20, max_length=200_000)


class DocumentOut(BaseModel):
    doc_id: str
    title: str
    product: str
    version: str
    source_type: str
    trust_level: str
    filename: str
    chunk_count: int
    indexed_at: datetime


class FeedbackIn(BaseModel):
    user_id: str
    message_id: str
    helpful: bool
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    feedback_id: str
    message_id: str
    helpful: bool
    trace_id: str | None = None


class TicketIn(BaseModel):
    user_id: str
    category: Literal["security", "credentials", "privacy", "legal", "data_loss", "billing", "multi_user",
                      "outage", "unresolved", "sync", "general"]
    summary: str = Field(min_length=5, max_length=2000)
    product_area: str | None = None
    steps_tried: list[str] = Field(default_factory=list)
    error_message: str | None = None
    related_ids: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    priority: Literal["low", "normal", "high"] = "normal"


class TicketOut(BaseModel):
    ticket_id: str
    category: str
    priority: str
    status: str
    tool_event: ToolEventOut


class EvalRunIn(BaseModel):
    include_extra: bool = True
    case_ids: list[str] | None = None
    use_deepeval: bool = True


class EvalRunOut(BaseModel):
    run_id: str
    status: str
    summary: dict = Field(default_factory=dict)
    report_path: str | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    llm_provider: str
    dependencies: dict[str, str]
    indexed_documents: int
    indexed_chunks: int


class ErrorOut(BaseModel):
    error: str
    detail: str
    request_id: str | None = None


class UserOut(BaseModel):
    user_id: str
    display_name: str
    account_id: str | None
    plan: str | None = None
    verified: bool = False


class MetricsSummary(BaseModel):
    window: str
    total_requests: int
    error_count: int
    error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    escalation_rate: float
    negative_feedback_rate: float
    routes: dict[str, int]
    total_tokens: int
    alerts: list[str]
    recent: list[dict]
