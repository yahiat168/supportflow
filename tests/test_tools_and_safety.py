import json
from pathlib import Path

import pytest

from app.graph.router import apply_rules, extract_entities
from app.tools.redaction import find_secrets, redact

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("case", json.loads((ROOT / "evals/goldens.json").read_text()), ids=lambda c: c["id"])
def test_rules_route_goldens(case, client):
    """Hard safety rules route every escalation / tool case (the LLM is only used for soft cases)."""
    d = apply_rules(case["input"], extract_entities(case["input"]))
    expected = case["expected_route"][0]
    if expected in ("escalation", "account_tool", "status_tool"):
        assert d.route == expected and d.hard
    else:
        assert d.route in (None, expected)


def test_redaction():
    text = "card 4242 4242 4242 4242 cvv 123 my password is s3cret! and code 123456"
    red = redact(text)
    for secret in ("4242 4242", "cvv 123", "s3cret", "123456"):
        assert secret not in red
    assert set(find_secrets(text)) >= {"card_number", "card_security_code", "password", "one_time_code"}
    assert redact("order ord_7001 invoice inv_5011") == "order ord_7001 invoice inv_5011"
    assert find_secrets("my phone is 1234 5678 9012") == []  # fails Luhn -> not a card


def _auth(user):
    from app.tools.support_tools import resolve_auth
    return resolve_auth(user)


def test_scope_checks(client):
    from app.tools.support_tools import get_order, get_thread_summary, get_ticket

    assert get_order(_auth("user_maya"), order_id="ord_7001").ok
    assert get_order(_auth("user_maya"), order_id="ord_7003").error_code == "SCOPE_DENIED"
    assert get_order(_auth("user_maya"), order_id="ord_7001", account_id="acc_1003").error_code == "SCOPE_DENIED"
    assert get_order(_auth("user_omar"), order_id="ord_7002").error_code == "UNVERIFIED"
    assert get_order(_auth("user_guest"), order_id="ord_7001").error_code == "NO_ACCOUNT"
    assert get_order(_auth("user_maya"), invoice_id="inv_5014").error_code == "NOT_FOUND"
    assert get_ticket(_auth("user_maya"), "SUP-3001").ok
    assert get_ticket(_auth("user_maya"), "SUP-3003").error_code == "SCOPE_DENIED"
    from app.services.chat_service import create_thread
    tid = create_thread("user_nour").thread_id
    assert get_thread_summary(_auth("user_maya"), tid).error_code == "THREAD_DENIED"


def test_timeout_and_retry(client):
    from app.tools.base import set_fault_injection
    from app.tools.support_tools import get_order, get_service_status

    set_fault_injection({"get_order": "timeout", "get_service_status": "error"})
    try:
        r = get_order(_auth("user_maya"), order_id="ord_7001")
        assert not r.ok and r.error_code == "TIMEOUT" and r.event.attempts == 2
        s = get_service_status()
        assert not s.ok and s.error_code == "TOOL_ERROR"
    finally:
        set_fault_injection({})


def test_denials_are_not_retried(client):
    from app.tools.support_tools import get_order
    r = get_order(_auth("user_maya"), order_id="ord_7003")
    assert r.event.attempts == 1 and r.event.status == "denied"


def test_search_top_k_is_bounded(client):
    from app.tools.support_tools import search_knowledge_base
    r = search_knowledge_base("plans", top_k=100)
    assert r.ok and len(r.data.chunks) <= 8


def test_critic_blocks_promises_secrets_and_leaks():
    from app.graph.subagents.critic import deterministic_checks

    base = {"route": "escalation", "request": "refund please", "auth": {"account_id": "acc_1001", "email": "maya@example.com"},
            "evidence": [], "tool_events": [], "escalation": {}}
    assert any("promise" in i for i in deterministic_checks({**base, "draft_answer": "You will receive a full refund."}))
    assert any("promise" in i for i in deterministic_checks({**base, "draft_answer": "It will be fixed within 2 hours."}))
    assert any("secret" in i for i in deterministic_checks({**base, "draft_answer": "Please send me your one-time code."}))
    assert not deterministic_checks({**base, "draft_answer": "Please don't share your password in chat."})
    assert any("scope" in i for i in deterministic_checks({**base, "draft_answer": "Order ord_7003 costs 249 USD."}))
    assert any("email" in i for i in deterministic_checks({**base, "draft_answer": "Contact nour@example.com."}))
    assert any("ticket ID" in i for i in deterministic_checks(
        {**base, "draft_answer": "Escalated.", "escalation": {"ticket_id": "SUP-9999"}}))


def test_graph_is_bounded(client):
    from app.graph.orchestrator import get_graph
    g = get_graph().get_graph()
    assert {"orchestrator", "knowledge", "troubleshooting", "account_tools", "status", "escalation", "critic",
            "revise", "finalize"} <= set(g.nodes)
