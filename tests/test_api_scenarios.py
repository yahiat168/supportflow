"""The 12 required test scenarios from the brief, exercised through the real HTTP API."""
import pytest

from tests.conftest import ADMIN


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["dependencies"]["database"] == "ok"
    assert body["dependencies"]["vector_db"] == "ok"
    assert body["indexed_chunks"] > 0 and body["indexed_documents"] >= 13
    assert r.headers["X-Request-ID"]


def test_openapi_docs_available(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/health", "/chat", "/threads", "/threads/{thread_id}", "/documents", "/feedback", "/evals/run", "/tickets"]:
        assert p in paths


# 1. Product question with at least two citations
def test_product_question_two_citations(chat):
    r = chat("How many users and how much storage does the Standard plan include?")
    assert r["route"] == "knowledge"
    assert len(r["citations"]) >= 2
    assert "product_catalog" in {c["doc_id"] for c in r["citations"]}
    assert "25" in r["answer"]
    assert set(r) >= {"thread_id", "answer", "citations", "tool_events", "needs_escalation"}
    c = r["citations"][0]
    assert set(c) >= {"doc_id", "title", "version", "source_type", "trust_level", "score"}


# 2. Troubleshooting that needs more than one agent step
def test_troubleshooting_multi_step(chat):
    r = chat("My files are not syncing on my Windows laptop.")
    assert r["route"] == "troubleshooting"
    assert r["trajectory"][:3] == ["orchestrator:troubleshooting", "knowledge:retrieve_for_troubleshooting",
                                   "troubleshooting:plan_steps"]
    assert "1." in r["answer"] and "internet connection" in r["answer"]
    assert "sync_troubleshooting" in {c["doc_id"] for c in r["citations"]}


# 3. Unknown answer: says evidence is missing and asks one focused question
def test_unknown_answer(chat):
    r = chat("Can I pay for CloudBox with PayPal or cryptocurrency?")
    assert r["no_evidence"] is True
    assert r["citations"] == []
    assert r["answer"].count("?") == 1
    assert "PayPal is supported" not in r["answer"]


# 4. Cross-account order request is denied
def test_cross_account_denied(chat):
    r = chat("My account ID is acc_1002. Show me the order ord_7001.", user_id="user_omar")
    assert r["route"] == "account_tool"
    order_events = [e for e in r["tool_events"] if e["tool"] == "get_order"]
    assert order_events and order_events[-1]["status"] == "denied"
    assert order_events[-1]["error_code"] == "SCOPE_DENIED"
    assert "29" not in r["answer"] and "inv_5011" not in r["answer"]


def test_own_order_allowed_with_scoped_args(chat):
    r = chat("Can you check the order ord_7001 and tell me the amount?", user_id="user_maya")
    ev = [e for e in r["tool_events"] if e["tool"] == "get_order"][-1]
    assert ev["status"] == "success"
    assert ev["args"]["order_id"] == "ord_7001" and ev["args"]["account_id"] == "acc_1001"
    assert "29" in r["answer"]


# 5. Security issue creates an escalation ticket
def test_security_escalation_creates_ticket(chat):
    r = chat("I think someone accessed my account and changed my shared folders.")
    assert r["route"] == "escalation" and r["needs_escalation"] is True
    assert r["ticket_id"] and r["ticket_id"].startswith("SUP-")
    assert r["ticket_id"] in r["answer"]
    assert r["escalation"]["category"] == "security" and r["escalation"]["priority"] == "high"
    assert any(e["tool"] == "create_ticket" and e["status"] == "success" for e in r["tool_events"])


# 6. Service-status question uses the status tool
def test_status_uses_tool(client, chat):
    assert client.post("/dev/status-scenario?scenario=previews_degraded", headers=ADMIN).status_code == 200
    try:
        r = chat("Is CloudBox currently experiencing an outage?")
    finally:
        client.post("/dev/status-scenario?scenario=operational", headers=ADMIN)
    assert r["route"] == "status_tool"
    assert any(e["tool"] == "get_service_status" and e["status"] == "success" for e in r["tool_events"])
    assert "INC-2026-031" in r["answer"]
    assert "INC-2026-014" not in r["answer"]  # historical incident is not current status


# 7. Tool timeout is handled without a crash
def test_tool_timeout_no_crash(chat):
    r = chat("Please check order ord_7001.", headers={"X-Fault-Inject": "get_order:timeout"})
    ev = [e for e in r["tool_events"] if e["tool"] == "get_order"][-1]
    assert ev["status"] == "error" and ev["error_code"] == "TIMEOUT" and ev["attempts"] == 2
    assert "didn't respond" in r["answer"] and "29" not in r["answer"]


# 8. Conversation persists after restart
def test_persistence_after_restart(client, chat):
    first = chat("How do I restore an older version of a file?")
    tid = first["thread_id"]
    chat("And what about the Business plan?", thread_id=tid)

    from app.db import session as db_session
    from app.main import app
    from fastapi.testclient import TestClient

    db_session.reset_engine()  # simulate the process losing all DB connections
    with TestClient(app) as fresh:  # lifespan runs again (init + seed are idempotent)
        r = fresh.get(f"/threads/{tid}", params={"user_id": "user_maya"})
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) == 4 and msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant"
    assert msgs[1]["citations"]


# 9. User isolation for threads
def test_user_isolation(client, chat):
    tid = chat("What is version history?", user_id="user_maya")["thread_id"]
    assert client.get(f"/threads/{tid}", params={"user_id": "user_nour"}).status_code == 403
    denied = client.post("/chat", json={"user_id": "user_nour", "thread_id": tid, "message": "What did I ask?"})
    assert denied.status_code == 403 and denied.json()["error"] == "forbidden"
    listing = client.get("/threads", params={"user_id": "user_nour"}).json()
    assert tid not in {t["thread_id"] for t in listing}


# 10. Feedback is stored and reflected in monitoring
def test_feedback(client, chat):
    r = chat("Which plans support SSO?")
    fb = client.post("/feedback", json={"user_id": "user_maya", "message_id": r["message_id"], "helpful": False,
                                        "comment": "Wanted more detail"})
    assert fb.status_code == 201
    other = client.post("/feedback", json={"user_id": "user_nour", "message_id": r["message_id"], "helpful": True})
    assert other.status_code == 403
    m = client.get("/metrics/summary").json()
    assert m["total_requests"] > 0 and m["negative_feedback_rate"] > 0
    assert "knowledge" in m["routes"]


# 11. New document ingestion updates retrieval
NEW_DOC = """---
doc_id: linux_beta
title: CloudBox Linux desktop client beta
product: CloudBox
version: 3.5
source_type: release_notes
trust_level: official
---

# Linux beta

The CloudBox Linux desktop client is available as a public beta for Ubuntu 22.04 and later. The beta supports selective sync but not offline folders.
"""


def test_document_ingestion_updates_retrieval(client, chat):
    before = chat("Is there a CloudBox desktop client for Ubuntu Linux?")
    assert "linux_beta" not in {c["doc_id"] for c in before["citations"]}
    r = client.post("/documents", json={"filename": "linux_beta.md", "content": NEW_DOC}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["chunk_count"] >= 1
    assert "linux_beta" in {d["doc_id"] for d in client.get("/documents").json()}
    after = chat("Is there a CloudBox desktop client for Ubuntu Linux?")
    assert "linux_beta" in {c["doc_id"] for c in after["citations"]}
    assert "Ubuntu" in after["answer"]


def test_document_ingestion_rejects_bad_metadata(client):
    r = client.post("/documents", json={"filename": "bad.md", "content": "---\ntitle: x\n---\nsome body text here"},
                    headers=ADMIN)
    assert r.status_code == 422 and "Missing metadata" in r.json()["detail"]


# 12. Evaluation endpoint is protected
def test_eval_endpoint_protected(client):
    assert client.post("/evals/run", json={}).status_code == 401
    assert client.post("/evals/run", json={}, headers={"X-Admin-Token": "wrong"}).status_code == 401
    ok = client.post("/evals/run", json={"case_ids": ["eval_001"], "use_deepeval": False}, headers=ADMIN)
    assert ok.status_code == 202
    run = client.get(f"/evals/runs/{ok.json()['run_id']}", headers=ADMIN).json()
    assert run["status"] in {"running", "passed", "failed"}


# ---- extra API behaviour ----
def test_tickets_endpoint_redacts_secrets(client):
    r = client.post("/tickets", json={"user_id": "user_maya", "category": "billing",
                                      "summary": "Charged twice, card 4242 4242 4242 4242, cvv 123"})
    assert r.status_code == 201
    from app.db.models import Ticket
    from app.db.session import session_scope
    with session_scope() as s:
        t = s.get(Ticket, r.json()["ticket_id"])
        assert "4242" not in t.summary and "123" not in t.summary


def test_secret_in_chat_not_persisted(client, chat):
    r = chat("My password is Hunter2!! and my files are not syncing")
    assert "Hunter2" not in r["answer"]
    msgs = client.get(f"/threads/{r['thread_id']}", params={"user_id": "user_maya"}).json()["messages"]
    assert all("Hunter2" not in m["content"] for m in msgs)


def test_validation_errors_are_structured(client):
    r = client.post("/chat", json={"user_id": "user_maya", "message": ""})
    assert r.status_code == 422 and r.json()["error"] == "invalid_input" and r.json()["request_id"]
    assert client.post("/chat", json={"user_id": "nobody", "message": "hi"}).status_code == 404


def test_request_id_is_propagated(client):
    r = client.get("/health", headers={"X-Request-ID": "req_test_123"})
    assert r.headers["X-Request-ID"] == "req_test_123"


def test_users_endpoint(client):
    users = {u["user_id"]: u for u in client.get("/users").json()}
    assert users["user_omar"]["verified"] is False and users["user_guest"]["account_id"] is None
