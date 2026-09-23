"""Critic + response agent: checks evidence, citations, safety and scope, then finalizes the answer.

Deterministic guardrails always run (they cannot be talked out of a rejection). In online mode an
LLM groundedness check runs as well, within the model-call budget.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph import llm
from app.graph.common import NO_EVIDENCE_PREFIX, format_evidence
from app.graph.prompts import CRITIC_SYSTEM, REVISE_SYSTEM
from app.graph.router import extract_entities
from app.graph.state import Evidence, SupportState
from app.observability.tracing import observation

log = logging.getLogger("supportflow.critic")

_NEG = re.compile(r"\b(don'?t|do not|never|not|won'?t|can'?t|cannot|no need|avoid)\b", re.I)
SECRET_REQUEST = re.compile(
    r"\b(send|share|provide|give|tell|enter|type|confirm|reply with)\b[^.?!]{0,30}\b(password|one[- ]time code|otp|"
    r"verification code|cvv|cvc|security code|card number)s?\b", re.I)
PROMISES = [
    re.compile(r"\b(you will|you'll|we will|we'll)\b[^.?!]{0,30}\b(receive|get|be issued|be given)\b[^.?!]{0,15}\brefund", re.I),
    re.compile(r"\brefund\b[^.?!]{0,20}\b(has been|will be|is) (approved|issued|processed|guaranteed)", re.I),
    re.compile(r"\b(will be|should be|expected to be) (resolved|fixed|restored|back|back up|available)\b[^.?!]{0,20}\b(within|in|by)\b[^.?!]{0,15}\d", re.I),
    re.compile(r"\bwithin \d+ (minutes?|hours?|days?|business days)\b", re.I),
]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class CriticVerdict(BaseModel):
    supported: bool
    issues: list[str] = Field(default_factory=list)


class Revised(BaseModel):
    answer: str
    used_doc_ids: list[str] = Field(default_factory=list)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def deterministic_checks(state: SupportState) -> list[str]:
    draft = state.get("draft_answer", "") or ""
    route = state.get("route")
    evidence = [Evidence(**e) for e in state.get("evidence", [])]
    evidence_text = " ".join(e.content for e in evidence).lower()
    auth = state.get("auth", {})
    issues: list[str] = []

    if not draft.strip():
        issues.append("Empty answer.")
    if route in ("knowledge", "troubleshooting") and not evidence and not draft.startswith(NO_EVIDENCE_PREFIX):
        issues.append("Answer given without any retrieved evidence.")
    if route == "knowledge" and evidence and not state.get("no_evidence") and not state.get("used_doc_ids"):
        issues.append("Knowledge answer has no supporting citation.")
    for sent in _sentences(draft):
        if SECRET_REQUEST.search(sent) and not _NEG.search(sent):
            issues.append(f"Requests a secret: '{sent.strip()[:80]}'")
        for rx in PROMISES:
            m = rx.search(sent)
            if m and not _NEG.search(sent) and m.group(0).lower() not in evidence_text:
                issues.append(f"Unsupported promise: '{m.group(0)}'")

    # Account isolation: IDs/emails in the answer must belong to this user or come from their own message.
    allowed = {v for vals in extract_entities(state.get("request", "")).values() for v in vals}
    allowed |= {auth.get("account_id"), auth.get("workspace_id")}
    for ev in state.get("tool_events", []):
        if ev.get("status") == "success":
            allowed |= {v for vals in extract_entities(ev.get("summary", "")).values() for v in vals}
    esc = state.get("escalation") or {}
    allowed.add(esc.get("ticket_id"))
    allowed |= {v for vals in extract_entities(evidence_text).values() for v in vals}
    for vals in extract_entities(draft).values():
        for v in vals:
            if v not in allowed:
                issues.append(f"Mentions an identifier outside the user's scope: {v}")
    for email in EMAIL.findall(draft):
        if email.lower() != (auth.get("email") or "").lower():
            issues.append("Mentions an email address that is not the user's.")
    if esc.get("ticket_id") and esc["ticket_id"] not in draft:
        issues.append("Escalation answer does not state the ticket ID.")
    return issues


def critic_node(state: SupportState) -> dict:
    settings = get_settings()
    with observation("critic", as_type="guardrail", input={"route": state.get("route")}) as obs:
        issues = deterministic_checks(state)
        llm_checked = False
        if (not issues and not settings.is_offline and state.get("route") in ("knowledge", "troubleshooting")
                and not state.get("no_evidence")):
            try:
                ev = [Evidence(**e) for e in state.get("evidence", [])]
                verdict = llm.structured(CriticVerdict, CRITIC_SYSTEM,
                                         f"DRAFT:\n{state.get('draft_answer')}\n\nEVIDENCE:\n{format_evidence(ev)}",
                                         name="critic_grounding")
                llm_checked = True
                if not verdict.supported:
                    issues += [f"Grounding: {i}" for i in verdict.issues] or ["Grounding: unsupported claim"]
            except (llm.LLMUnavailable, llm.LLMBudgetExceeded) as exc:
                log.warning("critic LLM skipped: %s", exc)
        approved = not issues
        obs.update(output={"approved": approved, "issues": issues, "llm_checked": llm_checked},
                   level="DEFAULT" if approved else "WARNING")
        return {"critic": {"approved": approved, "issues": issues, "llm_checked": llm_checked},
                "trajectory": [f"critic:{'approved' if approved else 'rejected'}"]}


def after_critic(state: SupportState) -> str:
    if state.get("critic", {}).get("approved"):
        return "finalize"
    if state.get("revisions", 0) < get_settings().max_critic_revisions:
        return "revise"
    return "finalize"


def revise_node(state: SupportState) -> dict:
    issues = state.get("critic", {}).get("issues", [])
    draft = state.get("draft_answer", "")
    used = state.get("used_doc_ids", [])
    with observation("revise", as_type="agent", input={"issues": issues}) as obs:
        new = None
        if not get_settings().is_offline:
            try:
                ev = [Evidence(**e) for e in state.get("evidence", [])]
                tool_facts = [e.get("summary") for e in state.get("tool_events", []) if e.get("status") == "success"]
                out = llm.structured(Revised, REVISE_SYSTEM,
                                     f"DRAFT:\n{draft}\n\nISSUES:\n{issues}\n\nTOOL FACTS:\n{tool_facts}\n\n"
                                     f"EVIDENCE:\n{format_evidence(ev)}", name="revise_answer")
                valid = {e.doc_id for e in ev}
                new, used = out.answer, [d for d in out.used_doc_ids if d in valid] or used
            except (llm.LLMUnavailable, llm.LLMBudgetExceeded) as exc:
                log.warning("revise LLM fallback: %s", exc)
        if new is None:
            new = _strip_bad_sentences(state)
        obs.update(output=new[:300])
        return {"draft_answer": new, "used_doc_ids": used, "revisions": state.get("revisions", 0) + 1,
                "trajectory": ["response:revise"]}


def _strip_bad_sentences(state: SupportState) -> str:
    keep = []
    for sent in _sentences(state.get("draft_answer", "")):
        bad = (SECRET_REQUEST.search(sent) and not _NEG.search(sent)) or any(
            rx.search(sent) and not _NEG.search(sent) for rx in PROMISES)
        probe = dict(state, draft_answer=sent, escalation={})
        if not bad and not any("outside the user's scope" in i or "email" in i for i in deterministic_checks(probe)):
            keep.append(sent.strip())
    return " ".join(keep)


SAFE_FALLBACK = ("I couldn't produce an answer that I can fully support with CloudBox sources, so I'd rather not guess. "
                 "Could you rephrase your question or tell me which feature it's about?")


def finalize_node(state: SupportState) -> dict:
    critic = state.get("critic", {})
    draft = (state.get("draft_answer") or "").strip()
    esc = state.get("escalation") or {}
    no_evidence = bool(state.get("no_evidence"))
    if not critic.get("approved"):
        # Final rejection after bounded revisions: never ship the unsafe text.
        draft = SAFE_FALLBACK if not esc.get("ticket_id") else f"I've created escalation ticket {esc['ticket_id']} so the team can follow up."
        no_evidence = not esc.get("ticket_id")
    evidence = [Evidence(**e) for e in state.get("evidence", [])]
    by_doc: dict[str, Evidence] = {}
    for e in sorted(evidence, key=lambda x: -x.score):
        by_doc.setdefault(e.doc_id, e)
    used = [d for d in (state.get("used_doc_ids") or []) if d in by_doc]
    citations = [] if no_evidence and not esc.get("needs_escalation") else [
        {"doc_id": d, "title": by_doc[d].title, "version": by_doc[d].version, "source_type": by_doc[d].source_type,
         "trust_level": by_doc[d].trust_level, "snippet": by_doc[d].content[:320], "score": by_doc[d].score}
        for d in used
    ]
    answer = draft
    if state.get("secrets_detected") and "removed the sensitive details" not in answer:
        answer = ("Note: your message contained sensitive details (for example a password, code, or card number). "
                  "I removed them and did not store them. Please never share these in chat.\n\n" + answer)
    # Internal staff instructions (agent playbook) stay in `citations` for the UI and audits, but are
    # not listed as customer-facing sources.
    public = [c for c in citations if c["source_type"] != "agent_instruction"]
    if public:
        answer += "\n\nSources: " + "; ".join(f"{c['title']} (v{c['version']})" for c in public)
    return {"final_answer": answer, "citations": citations, "no_evidence": no_evidence,
            "escalation": {**{"needs_escalation": False}, **esc}, "trajectory": ["response:finalize"]}
