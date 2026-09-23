"""Troubleshooting subagent: turns retrieved technical evidence into ordered diagnostic steps."""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph import llm
from app.graph.common import format_evidence, result_update, split_sentences
from app.graph.prompts import TROUBLESHOOT_SYSTEM
from app.graph.state import Evidence, SubagentResult, SupportState
from app.graph.subagents.knowledge import no_evidence_answer
from app.observability.tracing import observation
from app.rag.embeddings import tokenize

log = logging.getLogger("supportflow.troubleshooting")
DIAGNOSTIC_QUESTIONS = [
    "your operating system",
    "your CloudBox desktop client version",
    "whether this affects only you or the whole workspace",
]


class TroubleshootPlan(BaseModel):
    summary: str
    steps: list[str]
    diagnostic_questions: list[str] = Field(default_factory=list)
    escalate_if: str | None = None
    used_doc_ids: list[str] = Field(default_factory=list)


def troubleshooting_node(state: SupportState) -> dict:
    evidence = [Evidence(**e) for e in state.get("evidence", [])]
    query = state.get("standalone_query") or state["request"]
    with observation("troubleshooting_subagent", as_type="agent", input={"query": query}) as obs:
        if not evidence:
            result = SubagentResult(agent="troubleshooting", status="no_evidence", summary="No troubleshooting evidence",
                                    draft_answer=no_evidence_answer(
                                        "Which CloudBox feature is affected, and what exactly do you see (an error message, missing files, or something else)?"),
                                    missing_information=["affected feature and exact symptom"])
            obs.update(output=result.summary)
            return result_update(result, no_evidence=True, trajectory=["troubleshooting:no_evidence"])
        plan = _plan(query, evidence)
        lines = [plan.summary.strip(), ""]
        lines += [f"{i}. {s.strip()}" for i, s in enumerate(plan.steps, 1)]
        if plan.diagnostic_questions:
            lines += ["", "To narrow it down, please tell me " + _join(plan.diagnostic_questions) + "."]
        if plan.escalate_if:
            lines += ["", plan.escalate_if.strip()]
        result = SubagentResult(agent="troubleshooting", status="ok", summary=f"{len(plan.steps)} ordered steps",
                                evidence=evidence, draft_answer="\n".join(lines).strip(),
                                used_doc_ids=plan.used_doc_ids, missing_information=plan.diagnostic_questions)
        obs.update(output=plan.model_dump())
        upd = result_update(result, no_evidence=False, trajectory=["troubleshooting:plan_steps"])
        upd.pop("evidence")  # evidence already in state from the knowledge step
        return upd


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + ", and " + items[-1]


def _plan(query: str, evidence: list[Evidence]) -> TroubleshootPlan:
    if not get_settings().is_offline:
        try:
            out = llm.structured(TroubleshootPlan, TROUBLESHOOT_SYSTEM,
                                 f"User problem: {query}\n\nEvidence:\n{format_evidence(evidence)}",
                                 name="troubleshooting_plan")
            valid = {e.doc_id for e in evidence}
            out.used_doc_ids = [d for d in out.used_doc_ids if d in valid] or [evidence[0].doc_id]
            if out.steps:
                return out
        except (llm.LLMUnavailable, llm.LLMBudgetExceeded) as exc:
            log.warning("troubleshooting LLM fallback: %s", exc)
    return _offline_plan(query, evidence)


def _offline_plan(query: str, evidence: list[Evidence]) -> TroubleshootPlan:
    q = set(tokenize(query))
    ranked = sorted(evidence, key=lambda e: (e.source_type == "troubleshooting",
                                             len(q & set(tokenize(e.content))), e.score), reverse=True)
    best = ranked[0]
    if re.search(r"^\s*\d+\.", best.content, re.M):
        steps = [re.sub(r"^\s*\d+\.\s*", "", l).strip() for l in best.content.splitlines() if re.match(r"^\s*\d+\.", l)]
        intro = best.content.splitlines()[0].rstrip(":").rstrip(".") + ":"
    else:
        steps = split_sentences(best.content)
        intro = "Here is the documented way to handle this, in order:"
    escalate = next((e.content for e in evidence if e.doc_id == best.doc_id and "Escalate" in e.content
                     and e.content != best.content), None)
    return TroubleshootPlan(
        summary=intro, steps=steps,
        diagnostic_questions=DIAGNOSTIC_QUESTIONS if best.source_type == "troubleshooting" else [],
        escalate_if=escalate, used_doc_ids=[best.doc_id],
    )
