"""Escalation subagent: applies the escalation policy and creates a structured, redacted ticket."""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph import llm
from app.graph.common import format_evidence, pinned_evidence, result_update
from app.graph.prompts import ESCALATION_SYSTEM
from app.graph.router import CATEGORY_DOCS
from app.graph.state import Evidence, SubagentResult, SupportState
from app.observability.tracing import observation
from app.tools.redaction import redact
from app.tools.support_tools import AuthContext, TicketInput, create_ticket, get_order

log = logging.getLogger("supportflow.escalation")

PRIORITY = {"security": "high", "credentials": "high", "data_loss": "high", "legal": "high",
            "multi_user": "high", "outage": "high", "privacy": "normal", "billing": "normal", "unresolved": "normal"}
PRODUCT_AREA_WORDS = {"sync": "desktop sync", "preview": "file previews", "share": "sharing", "sharing": "sharing",
                      "upload": "uploads", "notification": "notifications", "login": "sign-in", "sign in": "sign-in"}


class EscalationReply(BaseModel):
    answer: str
    used_doc_ids: list[str] = Field(default_factory=list)


def _product_area(category: str, text: str) -> str:
    fixed = {"security": "account security", "credentials": "sign-in", "privacy": "privacy",
             "billing": "billing", "legal": "legal", "data_loss": "files and trash"}
    if category in fixed:
        return fixed[category]
    low = text.lower()
    return next((area for word, area in PRODUCT_AREA_WORDS.items() if word in low), "general")


def _missing_info(category: str, text: str, auth: AuthContext, invoice_ids: list[str]) -> list[str]:
    low = text.lower()
    missing = []
    if category in ("multi_user", "unresolved", "outage"):
        if not re.search(r"\b(since|started|at \d|yesterday|today|this morning|\d{1,2}:\d{2})\b", low):
            missing.append("when the problem started (approximate time)")
        if "error" not in low:
            missing.append("the exact error message, if any")
        if not re.search(r"\b(tried|restart|reinstall|troubleshoot|steps)\b", low):
            missing.append("the steps you have already tried")
    if category == "billing":
        if not invoice_ids:
            missing.append("the invoice IDs involved")
        if not auth.email:
            missing.append("your account email")
    return missing


def escalation_node(state: SupportState) -> dict:
    auth = AuthContext(**state["auth"])
    category = state.get("escalation_category") or "unresolved"
    text = state["request"]
    ents = state.get("entities", {})
    with observation("escalation_subagent", as_type="agent", input={"category": category}) as obs:
        evidence, ev_event = pinned_evidence(text, CATEGORY_DOCS.get(category, ["escalation_policy"]), 4)
        events = [ev_event]
        notes: list[str] = []
        related = list(dict.fromkeys(ents.get("invoice_ids", []) + ents.get("order_ids", []) + ents.get("ticket_ids", [])))

        # Billing: check the referenced invoices inside the user's own scope only.
        if category == "billing":
            for inv in ents.get("invoice_ids", [])[:3]:
                res = get_order(auth, invoice_id=inv)
                events.append(res.event.model_dump())
                if not res.ok and res.error_code == "NOT_FOUND":
                    notes.append(f"I couldn't find {inv} on your account, so the billing specialist will check it.")
                elif not res.ok and res.error_code == "SCOPE_DENIED":
                    notes.append(f"{inv} isn't linked to your account, so I haven't looked at it.")

        steps_tried = []
        if re.search(r"\b(tried|did ?n[o']t help|didn't work|still)\b", text, re.I):
            steps_tried.append("User reports basic troubleshooting was already tried")
        if any(t.get("route") == "troubleshooting" for t in state.get("history", [])):
            steps_tried.append("Documented troubleshooting steps were provided earlier in this thread")
        err = re.search(r"error[^.]*", text, re.I)
        missing = _missing_info(category, text, auth, ents.get("invoice_ids", []))
        status_incident = next((e for e in state.get("tool_events", []) if e["tool"] == "get_service_status"), None)

        ticket_in = TicketInput(
            category=category, priority=PRIORITY.get(category, "normal"), summary=redact(text)[:500],
            product_area=_product_area(category, text), steps_tried=steps_tried,
            error_message=redact(err.group(0))[:300] if err else None, related_ids=related,
            thread_id=state.get("thread_id"),
            details={"email_on_file": bool(auth.email), "workspace_id": auth.workspace_id,
                     "missing_information": missing,
                     "status_check": status_incident["summary"] if status_incident else None},
        )
        tres = create_ticket(auth, ticket_in)
        events.append(tres.event.model_dump())
        ticket_id = tres.data["ticket_id"] if tres.ok else None
        facts = {"category": category, "ticket_id": ticket_id, "ticket_created": tres.ok,
                 "workspace_id": auth.workspace_id, "related_ids": related, "notes": notes,
                 "missing_information": missing, "secrets_detected": state.get("secrets_detected", [])}
        answer, used = _reply(state, evidence, facts)
        prefix = state.get("draft_answer") if state.get("route") == "status_tool" else ""
        draft = f"{prefix}\n\n{answer}".strip() if prefix else answer
        escalation = {"needs_escalation": True, "category": category, "ticket_id": ticket_id,
                      "priority": ticket_in.priority, "reason": state.get("route_reason", ""),
                      "ticket_error": None if tres.ok else tres.error_code}
        result = SubagentResult(agent="escalation", status="escalated" if tres.ok else "error",
                                summary=f"{category} escalation -> {ticket_id or 'ticket failed'}",
                                evidence=evidence, tool_events=events, needs_escalation=True, draft_answer=draft,
                                used_doc_ids=used, missing_information=missing)
        obs.update(output=escalation)
        upd = result_update(result, no_evidence=False, trajectory=[f"escalation:{category}"], escalation=escalation)
        if state.get("route") == "status_tool":  # keep status evidence as well
            upd["evidence"] = state.get("evidence", []) + upd["evidence"]
            upd["used_doc_ids"] = list(dict.fromkeys(state.get("used_doc_ids", []) + used))
        return upd


def _reply(state: SupportState, evidence: list[Evidence], facts: dict) -> tuple[str, list[str]]:
    if not get_settings().is_offline:
        try:
            out = llm.structured(
                EscalationReply, ESCALATION_SYSTEM,
                f"User message: {state['request']}\n\nTicket facts: {facts}\n\nPolicy evidence:\n{format_evidence(evidence)}",
                name="escalation_reply",
            )
            valid = {e.doc_id for e in evidence}
            used = [d for d in out.used_doc_ids if d in valid] or [e.doc_id for e in evidence][:2]
            if not facts["ticket_id"] or facts["ticket_id"] in out.answer:
                return out.answer, used
        except (llm.LLMUnavailable, llm.LLMBudgetExceeded) as exc:
            log.warning("escalation LLM fallback: %s", exc)
    return _template(state, facts), list(dict.fromkeys(e.doc_id for e in evidence))[:3]


def _template(state: SupportState, f: dict) -> str:
    cat, tid, text = f["category"], f["ticket_id"], state["request"].lower()
    ticket = (f"I've created escalation ticket {tid}." if tid else
              "I tried to create an escalation ticket, but the ticket system didn't respond. Please try again in a "
              "moment so the team receives your case.")
    no_secrets = "Please don't share passwords, one-time codes, or full card details in this chat."
    body = {
        "security": f"This looks like a possible account takeover, so it has been escalated immediately to the security team. {ticket} "
                    f"{no_secrets} Two-factor authentication is available on all plans once you regain control of your account. "
                    "I can't promise a resolution time.",
        "credentials": "I can't see, reveal, or reset passwords, and CloudBox support never asks for your password or a "
                       f"one-time code in chat. {ticket} The security team will help you regain access through verified "
                       "account recovery. Two-factor authentication is available on all plans.",
        "privacy": ("You can request an export of your personal data from Settings > Privacy > Request data export. The export "
                    "is prepared asynchronously and is normally available within seven days, but I can't promise an exact "
                    f"completion time. Because privacy requests must be verified, I've also escalated this to the privacy team. {ticket}")
                   if "export" in text else
                   ("Account and data deletion requests are privacy requests, so they must be escalated for verification "
                    f"before anything is deleted. {ticket} I can't promise a completion time."),
        "billing": (f"A duplicate charge needs review by a billing specialist, so I've created a billing escalation with your invoice "
                    f"IDs ({', '.join(i for i in f['related_ids'] if i.startswith('inv_')) or 'not provided yet'}). {ticket} "
                    f"I can't promise that the charge will be refunded. {no_secrets}")
                   if re.search(r"twice|duplicate|double", text) else
                   ("Renewal charges are normally not refundable, and refunds requested more than 30 days after a charge need "
                    f"manual review by a billing specialist. I can't promise a refund, but I've requested that review for you. "
                    f"{ticket} {no_secrets}"),
        "legal": f"Legal requests are escalated immediately to the responsible team. {ticket} I can't promise a response time.",
        "data_loss": f"Possible data loss is escalated immediately. {ticket} Deleted files stay in Trash for 30 days on Starter "
                     "and Standard, 90 days on Team, and 180 days on Business, so please check Trash too. I can't promise "
                     "that files outside that period can be recovered.",
    }.get(cat)
    if body is None:  # multi_user / unresolved / outage
        why = "it affects multiple users" if cat in ("multi_user", "outage") else "it persists after the documented steps"
        body = (f"Because {why}, this needs escalation. {ticket}"
                + (f" The ticket includes your workspace ID ({f['workspace_id']})." if f.get("workspace_id") else "")
                + " I can't give a resolution time unless the service status system publishes one.")
    extras = " ".join(f["notes"])
    ask = (" To complete the escalation, please share: " + "; ".join(f["missing_information"]) + ".") if f["missing_information"] else ""
    warn = (" For your safety I removed the sensitive details you shared, and they weren't saved."
            if f.get("secrets_detected") else "")
    return f"{body} {extras}{ask}{warn}".replace("  ", " ").strip()
