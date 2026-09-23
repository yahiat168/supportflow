"""Top-level LangGraph orchestrator.

    orchestrator ─┬─ knowledge ──────────────┬─ critic ─┬─ finalize ─ END
                  │    └─(troubleshooting)─ troubleshooting ┘   └─ revise ─┘ (bounded)
                  ├─ account_tools ──────────┤
                  ├─ status ─(multi-user)─ escalation
                  └─ escalation ─────────────┘
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.config import get_settings
from app.graph import llm
from app.graph.prompts import CLASSIFIER_SYSTEM
from app.graph.router import apply_rules, extract_entities, is_follow_up
from app.graph.state import SupportState
from app.graph.subagents.account import account_node
from app.graph.subagents.critic import after_critic, critic_node, finalize_node, revise_node
from app.graph.subagents.escalation import escalation_node
from app.graph.subagents.knowledge import knowledge_node
from app.graph.subagents.status import status_node
from app.graph.subagents.troubleshooting import troubleshooting_node
from app.observability.tracing import observation

log = logging.getLogger("supportflow.orchestrator")

Category = Literal["security", "credentials", "privacy", "legal", "data_loss", "billing", "multi_user", "unresolved"]


class RouteClassification(BaseModel):
    route: Literal["knowledge", "troubleshooting", "account_tool", "escalation", "status_tool"]
    standalone_query: str
    reason: str
    escalation_category: Category | None = None


def orchestrator_node(state: SupportState) -> dict:
    request = state["request"]
    history = state.get("history", [])
    entities = extract_entities(request)
    decision = apply_rules(request, entities)
    prev_route = next((t.get("route") for t in reversed(history) if t.get("role") == "assistant" and t.get("route")), None)
    prev_user = next((t["content"] for t in reversed(history) if t.get("role") == "user"), None)
    follow_up = bool(prev_user) and is_follow_up(request)
    standalone = f"{prev_user} {request}" if follow_up else request

    with observation("orchestrator", as_type="agent",
                     input={"request": request, "history_turns": len(history)}) as obs:
        route, category, reason, source = decision.route, decision.category, decision.reason, "rules"
        # A user saying the documented steps did not help, right after a troubleshooting answer.
        if not decision.hard and decision.flags.get("unresolved") and prev_route == "troubleshooting":
            route, category, reason = "escalation", "unresolved", "Issue persists after documented troubleshooting"
        elif not decision.hard:
            route = None
            if not get_settings().is_offline:
                try:
                    hist = "\n".join(f"{t['role']}: {t['content'][:300]}" for t in history[-4:])
                    out = llm.structured(RouteClassification, CLASSIFIER_SYSTEM,
                                         f"History:\n{hist or '(none)'}\n\nLatest message: {request}\n"
                                         f"Rule hint: {decision.route or 'none'}", name="orchestrator_classify")
                    route, reason, source = out.route, out.reason, "llm"
                    standalone = out.standalone_query or standalone
                    category = out.escalation_category or ("unresolved" if route == "escalation" else None)
                except (llm.LLMUnavailable, llm.LLMBudgetExceeded) as exc:
                    log.warning("classifier fallback to rules: %s", exc)
            if route is None:
                source = "rules_fallback"
                if decision.route:
                    route = decision.route
                elif follow_up and prev_route in ("knowledge", "troubleshooting"):
                    route, reason = prev_route, "Follow-up to the previous answer"
                else:
                    route, reason = "knowledge", "Product or policy question"
            # Model cannot route private lookups without identifiers or downgrade hard rules.
            if route == "account_tool" and not any(entities.values()) and decision.route != "account_tool":
                route = "knowledge"
        obs.update(output={"route": route, "category": category, "source": source, "standalone_query": standalone})
    return {
        "route": route, "route_reason": reason, "route_source": source, "escalation_category": category,
        "flags": decision.flags, "entities": entities, "standalone_query": standalone,
        "focus_query": request if follow_up else standalone,
        "trajectory": [f"orchestrator:{route}"], "revisions": 0,
        "escalation": {"needs_escalation": route == "escalation", "category": category},
    }


def _route_edge(state: SupportState) -> str:
    return {"knowledge": "knowledge", "troubleshooting": "knowledge", "account_tool": "account_tools",
            "status_tool": "status", "escalation": "escalation"}[state["route"]]


def _after_knowledge(state: SupportState) -> str:
    return "troubleshooting" if state["route"] == "troubleshooting" else "critic"


def _after_status(state: SupportState) -> str:
    # Possible outage reported as affecting many users -> escalate per policy.
    if state.get("flags", {}).get("multi_user"):
        return "escalation"
    return "critic"


def build_graph():
    g = StateGraph(SupportState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("troubleshooting", troubleshooting_node)
    g.add_node("account_tools", account_node)
    g.add_node("status", status_node)
    g.add_node("escalation", _escalation_from_any)
    g.add_node("critic", critic_node)
    g.add_node("revise", revise_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges("orchestrator", _route_edge,
                            ["knowledge", "account_tools", "status", "escalation"])
    g.add_conditional_edges("knowledge", _after_knowledge, ["troubleshooting", "critic"])
    g.add_edge("troubleshooting", "critic")
    g.add_edge("account_tools", "critic")
    g.add_conditional_edges("status", _after_status, ["escalation", "critic"])
    g.add_edge("escalation", "critic")
    g.add_conditional_edges("critic", after_critic, ["revise", "finalize"])
    g.add_edge("revise", "critic")
    g.add_edge("finalize", END)
    return g.compile()


def _escalation_from_any(state: SupportState) -> dict:
    if state.get("route") == "status_tool" and not state.get("escalation_category"):
        state = {**state, "escalation_category": "outage"}
    upd = escalation_node(state)
    if state.get("route") == "status_tool":
        upd["escalation_category"] = state["escalation_category"]
    return upd


@lru_cache
def get_graph():
    return build_graph()
