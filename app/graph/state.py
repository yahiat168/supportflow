"""Shared LangGraph state. List fields use additive reducers so each node appends."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

Route = Literal["knowledge", "troubleshooting", "account_tool", "escalation", "status_tool"]
ROUTES: tuple[str, ...] = ("knowledge", "troubleshooting", "account_tool", "escalation", "status_tool")


class Evidence(BaseModel):
    doc_id: str
    title: str
    version: str
    source_type: str
    trust_level: str
    content: str
    score: float = 0.0
    section: str = ""


class SubagentResult(BaseModel):
    """Contract every subagent returns (docs/agent_contracts.md)."""
    agent: str
    status: Literal["ok", "no_evidence", "denied", "error", "needs_info", "escalated"]
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    needs_escalation: bool = False
    missing_information: list[str] = Field(default_factory=list)
    draft_answer: str = ""
    used_doc_ids: list[str] = Field(default_factory=list)


class SupportState(TypedDict, total=False):
    # identity / request
    request_id: str
    user_id: str
    thread_id: str
    request: str                     # user message (already redacted)
    history: list[dict[str, str]]    # recent turns from THIS thread only
    standalone_query: str
    focus_query: str                 # the new part of a follow-up question
    auth: dict[str, Any]
    secrets_detected: list[str]
    fault_injection: dict[str, str]
    # routing
    route: str
    route_reason: str
    route_source: str
    escalation_category: str | None
    flags: dict[str, Any]
    entities: dict[str, list[str]]
    # retrieval / evidence
    retrieved_context: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    # appended by nodes
    tool_events: Annotated[list[dict[str, Any]], operator.add]
    subagent_results: Annotated[list[dict[str, Any]], operator.add]
    trajectory: Annotated[list[str], operator.add]
    # escalation
    escalation: dict[str, Any]
    # answer
    draft_answer: str
    used_doc_ids: list[str]
    missing_information: list[str]
    no_evidence: bool
    critic: dict[str, Any]
    revisions: int
    final_answer: str
    citations: list[dict[str, Any]]
    # observability
    trace_id: str | None
