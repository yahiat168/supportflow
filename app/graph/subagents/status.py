"""Service-status handling. The live status tool is the source of truth for CURRENT state;
the incident-history document is cited only for the policy, never for current status."""
from __future__ import annotations

from app.graph.common import pinned_evidence, result_update
from app.graph.state import SubagentResult, SupportState
from app.observability.tracing import observation
from app.tools.support_tools import get_service_status


def status_node(state: SupportState) -> dict:
    with observation("status_check", as_type="agent", input={"request": state["request"]}) as obs:
        evidence, ev_event = pinned_evidence("status tool is the source of truth for current service state",
                                             ["status_incidents"], 2)
        res = get_service_status()
        events = [ev_event, res.event.model_dump()]
        missing: list[str] = []
        if not res.ok:
            draft = ("I couldn't reach the live service status system just now, so I can't confirm whether there is a "
                     "current incident. I won't rely on past incident history for this. Please try again in a few minutes.")
            status = "error"
        else:
            d = res.data
            incidents = d.get("active_incidents", [])
            if not incidents:
                draft = (f"The live status check shows no active incidents right now; all CloudBox components are "
                         f"operational (checked {d['checked_at']} UTC). If you're still seeing a problem, tell me what "
                         f"you see and whether it affects one person or your whole workspace.")
                missing = ["what you are seeing and how many people are affected"]
            else:
                lines = ["The live status check shows an active incident:"]
                for inc in incidents:
                    lines.append(f"- {inc['component']} ({inc['status']}, {inc['incident_id']}): {inc['message']} "
                                 f"Last update: {inc['updated_at']}.")
                if not any(inc.get("approved_eta") for inc in incidents):
                    lines.append("There is no approved recovery time yet, so I can't give an estimate. "
                                 "The status message above is the latest official update.")
                draft = "\n".join(lines)
            status = "ok"
        result = SubagentResult(agent="status", status=status, summary=res.message, evidence=evidence,
                                tool_events=events, draft_answer=draft, used_doc_ids=["status_incidents"] if evidence else [],
                                missing_information=missing)
        obs.update(output={"status": status, "draft": draft[:300]})
        return result_update(result, no_evidence=False, trajectory=[f"status:{status}"])
