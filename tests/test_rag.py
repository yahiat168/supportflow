from pathlib import Path

import pytest

from app.rag.chunker import chunk_markdown
from app.rag.loader import DocumentFormatError, load_directory, parse_markdown
from app.rag.versioning import compare_versions

ROOT = Path(__file__).resolve().parent.parent


def test_all_documents_have_valid_metadata():
    docs = load_directory(ROOT / "rag_materials")
    ids = {m.doc_id for m, _, _ in docs}
    assert {"product_catalog", "agent_playbook", "status_incidents", "escalation_policy"} <= ids
    assert len(docs) >= 13


def test_loader_rejects_missing_fields():
    with pytest.raises(DocumentFormatError):
        parse_markdown("---\ndoc_id: x\ntitle: y\n---\nbody")
    with pytest.raises(DocumentFormatError):
        parse_markdown("no metadata block")


def test_chunker_keeps_rule_with_its_steps():
    meta, body = parse_markdown((ROOT / "rag_materials/sync_troubleshooting.md").read_text())
    chunks = chunk_markdown(body)
    steps = next(c for c in chunks if "check these steps in order" in c.text)
    assert all(f"{i}." in steps.text for i in range(1, 7))  # lead-in and all six steps stay together


def test_chunker_keeps_tables_whole():
    _, body = parse_markdown((ROOT / "rag_materials/product_catalog.md").read_text())
    table = next(c for c in chunk_markdown(body) if "| Plan |" in c.text)
    for plan in ("Starter", "Standard", "Team", "Business"):
        assert plan in table.text


def test_chunker_faq_pairs_intact():
    _, body = parse_markdown((ROOT / "rag_materials/faq.md").read_text())
    for c in chunk_markdown(body):
        if "?**" in c.text:
            assert c.text.count("?**") == 1 and len(c.text.split("?**")[1].strip()) > 10


def test_version_comparison_mixed_formats():
    assert compare_versions("3.4", "3.2") == 1
    assert compare_versions("2026-02", "2026-01") == 1
    assert compare_versions("3.4", "2026-01") is None  # different formats are never compared


def test_retriever_filters_and_conflicts(client):
    from app.rag.retriever import search
    from app.rag.schemas import SearchFilters

    r = search("How many users does the Standard plan support?")
    assert r.chunks and all(c.trust_level in ("official", "internal") for c in r.chunks)
    assert any(c.superseded_doc_id == "product_catalog_v3_2" and c.preferred_doc_id == "product_catalog"
               for c in r.conflicts)
    community = search("connect Dropbox bridge script", SearchFilters(trust_level=["community"]))
    assert {c.doc_id for c in community.chunks} == {"community_tips"}
    default = search("connect Dropbox bridge script")
    assert "community_tips" not in {c.doc_id for c in default.chunks}  # excluded by default
    only_ts = search("files not syncing", SearchFilters(source_type=["troubleshooting"]))
    assert {c.source_type for c in only_ts.chunks} == {"troubleshooting"}


def test_retrieved_chunk_contract(client):
    from app.rag.retriever import search

    c = search("share a file with a coworker").chunks[0]
    assert set(c.model_dump()) >= {"doc_id", "title", "version", "source_type", "trust_level", "content", "score"}


def test_superseded_sources_ranked_last(client):
    from app.rag.retriever import search

    r = search("How many users does the Standard plan support?")
    ids = [c.doc_id for c in r.chunks]
    assert "product_catalog_v3_2" in ids
    first_old = ids.index("product_catalog_v3_2")
    assert all(d == "product_catalog_v3_2" for d in ids[first_old:])  # nothing current ranked below it


def test_relevance_window_trims_tail(client, monkeypatch):
    from app.config import reset_settings_cache
    from app.rag.retriever import search

    monkeypatch.setenv("RELEVANCE_WINDOW", "0.02")
    reset_settings_cache()
    try:
        r = search("How do I restore an older version of a file?")
        assert r.chunks and all(c.score >= r.chunks[0].score - 0.02 - 0.2 for c in r.chunks)
        assert len(r.chunks) <= 5
    finally:
        monkeypatch.delenv("RELEVANCE_WINDOW")
        reset_settings_cache()


def test_table_kept_with_its_notes():
    _, body = parse_markdown((ROOT / "rag_materials/product_catalog.md").read_text())
    chunks = chunk_markdown(body)
    table = next(c for c in chunks if "| Plan |" in c.text)
    assert "does not include SSO" in table.text  # the per-plan conditions stay with the numbers
    assert any("Downgrades take effect" in c.text and "| Plan |" not in c.text for c in chunks)
