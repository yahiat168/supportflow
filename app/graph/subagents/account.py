"""Account tools subagent: validates scope, then calls get_account / get_order / get_ticket.

Answers here are deterministic templates built from tool output (no model call), so private
data is never paraphrased, embellished, or mixed with another account's data.
"""
from __future__ import annotations

from app.graph.common import pinned_evidence, result_update
from app.graph.state import SubagentResult, SupportState
from app.observability.tracing import observation
from app.tools.support_tools import AuthContext, get_account, get_order, get_ticket

TOOL_NAMES = {"get_order": "order lookup", "get_account": "account lookup", "get_ticket": "ticket lookup"}
DENIAL_TEXT = {
    "SCOPE_DENIED": "I can't share details for {ref} because it isn't linked to the account you're signed in with. "
                    "For privacy, I can only look up orders, invoices, and tickets that belong to your own account.",
    "UNVERIFIED": "Your account hasn't completed verification yet, so I can't show private order or account details. "
                  "Please complete the verification step first, then ask me again.",
    "NO_ACCOUNT": "There's no CloudBox account linked to your sign-in, so I can't look up private order or account details.",
    "NOT_FOUND": "I couldn't find {ref} on your account. Please double-check the ID.",
    "INVALID_ARGS": "I need the order ID (for example ord_1234) or invoice ID (for example inv_1234) to look that up.",
    "TIMEOUT": "The {tool} service didn't respond in time, so I couldn't complete the lookup. Nothing was changed. "
               "Please try again in a moment.",
    "TOOL_ERROR": "The {tool} service returned an error, so I couldn't complete the lookup. Nothing was changed. "
                  "Please try again in a moment.",
}


def account_node(state: SupportState) -> dict:
    auth = AuthContext(**state["auth"])
    ents = state.get("entities", {})
    claimed_account = (ents.get("account_ids") or [None])[0]
    message = state["request"].lower()
    events: list[dict] = []
    with observation("account_tools_subagent", as_type="agent",
                     input={"entities": ents, "user_id": auth.user_id}) as obs:
        evidence, ev_event = pinned_evidence("use account or order tools only after verification", ["agent_playbook"], 2)
        events.append(ev_event)
        parts: list[str] = []
        status, missing = "ok", []

        lookups = [("order", o) for o in ents.get("order_ids", [])] + \
                  [("invoice", i) for i in ents.get("invoice_ids", [])] + \
                  [("ticket", t) for t in ents.get("ticket_ids", [])]
        if not lookups and any(w in message for w in ("plan", "account", "subscription", "workspace")):
            lookups = [("account", claimed_account)]
        if not lookups:
            status = "needs_info"
            missing = ["order ID or invoice ID"]
            parts.append("I can look that up for you. Which order ID or invoice ID should I check? "
                         "(You'll find it on your invoice email, for example ord_1234 or inv_1234.)")

        for kind, ref in lookups[:3]:  # bounded tool use
            if kind == "order":
                res = get_order(auth, order_id=ref, account_id=claimed_account)
            elif kind == "invoice":
                res = get_order(auth, invoice_id=ref, account_id=claimed_account)
            elif kind == "ticket":
                res = get_ticket(auth, ticket_id=ref)
            else:
                res = get_account(auth, account_id=claimed_account)
            events.append(res.event.model_dump())
            if res.ok:
                d = res.data
                if kind in ("order", "invoice"):
                    parts.append(f"Your account is verified and order {d['order_id']} belongs to it, so here are the "
                                 f"details: invoice {d['invoice_id']}, {d['plan']} plan, {d['amount_usd']:g} USD, "
                                 f"status {d['status']}, charged on {d['charged_at']}.")
                elif kind == "ticket":
                    parts.append(f"Ticket {d['ticket_id']} ({d['category']}) is currently {d['status']}: {d['summary']}.")
                else:
                    parts.append(f"Your account {d['account_id']} is on the {d['plan']} plan (workspace {d['workspace_id']}).")
            else:
                status = "denied" if res.event.status == "denied" else "error"
                tmpl = DENIAL_TEXT.get(res.error_code or "TOOL_ERROR", DENIAL_TEXT["TOOL_ERROR"])
                parts.append(tmpl.format(ref=ref or "that record", tool=TOOL_NAMES.get(res.event.tool, res.event.tool)))

        result = SubagentResult(agent="account_tools", status=status, summary=" | ".join(e["summary"] for e in events[1:]) or status,
                                evidence=evidence, tool_events=events, draft_answer=" ".join(dict.fromkeys(parts)),
                                used_doc_ids=[e.doc_id for e in evidence][:1], missing_information=missing)
        obs.update(output={"status": status, "tools": [e["tool"] + ":" + e["status"] for e in events]})
        return result_update(result, no_evidence=False, trajectory=[f"account_tools:{status}"],
                             escalation={"needs_escalation": False})
