"""Online (LLM) code paths with a stubbed model: structured outputs are wired correctly, and every
model failure degrades to the deterministic fallback instead of crashing."""
import pytest

from app.config import reset_settings_cache


@pytest.fixture
def online(monkeypatch, client):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    reset_settings_cache()
    yield
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    reset_settings_cache()


def _fake_structured(schema, system, user, name):
    from app.graph import llm
    from app.observability.tracing import current_stats
    current_stats().model_calls += 1
    if name == "orchestrator_classify":
        return schema(route="knowledge", standalone_query="Which plans include SSO?", reason="feature question")
    if name == "knowledge_answer":
        return schema(answer="SSO is available on the Business plan only (CloudBox plans and features).",
                      used_doc_ids=["product_catalog", "not_a_real_doc"])
    if name == "critic_grounding":
        return schema(supported=True, issues=[])
    raise llm.LLMUnavailable("unexpected call")


def test_online_knowledge_path(online, monkeypatch, chat):
    from app.graph import llm
    monkeypatch.setattr(llm, "structured", _fake_structured)
    r = chat("Tell me about single sign-on availability")
    assert r["route"] == "knowledge"
    assert "Business plan only" in r["answer"]
    assert [c["doc_id"] for c in r["citations"]] == ["product_catalog"]  # hallucinated doc_id dropped


def test_online_model_failure_falls_back(online, monkeypatch, chat):
    from app.graph import llm

    def boom(*a, **k):
        raise llm.LLMUnavailable("provider down")
    monkeypatch.setattr(llm, "structured", boom)
    r = chat("How do I restore an older version of a file?")
    assert r["route"] == "knowledge" and "Version history" in r["answer"]
    r2 = chat("I think someone accessed my account")
    assert r2["needs_escalation"] and r2["ticket_id"] in r2["answer"]


def test_online_critic_rejection_triggers_revision(online, monkeypatch, chat):
    from app.graph import llm
    calls = []

    def fake(schema, system, user, name):
        calls.append(name)
        if name == "orchestrator_classify":
            return schema(route="knowledge", standalone_query="refund policy", reason="policy")
        if name == "knowledge_answer":
            return schema(answer="Refunds: you will receive a full refund within 3 days.", used_doc_ids=["billing_refunds"])
        if name == "revise_answer":
            return schema(answer="New subscriptions can be refunded within 14 days; renewals are normally not refundable.",
                          used_doc_ids=["billing_refunds"])
        if name == "critic_grounding":
            return schema(supported=True, issues=[])
        raise AssertionError(name)
    monkeypatch.setattr(llm, "structured", fake)
    r = chat("What is the refund policy for new subscriptions?")
    assert "revise_answer" in calls
    assert "within 3 days" not in r["answer"] and "14 days" in r["answer"]
    assert "critic:rejected" in r["trajectory"] and "response:revise" in r["trajectory"]


def test_model_call_budget(online, monkeypatch):
    from app.graph import llm
    from app.observability.tracing import new_stats
    stats = new_stats("budget-test")
    stats.model_calls = 6
    with pytest.raises(llm.LLMBudgetExceeded):
        llm._check_budget()
