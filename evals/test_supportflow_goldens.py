"""DeepEval / pytest entrypoint, using the function names from the project brief.

    pytest evals -q                                  # deterministic gates (+ DeepEval if a judge key is set)
    deepeval test run evals/test_supportflow_goldens.py

Set SKIP_DEEPEVAL=1 to force deterministic-only scoring (CI without keys).
"""
import os

import pytest

from evals.runner import (_bootstrap, assert_critical_cases_pass, load_goldens, run_graph_for_dataset,
                          score_results, thresholds, write_json_report, write_markdown_report, ARTIFACTS)


@pytest.fixture(scope="module")
def evaluated():
    _bootstrap()
    dataset = load_goldens("evals/goldens.json")
    results = run_graph_for_dataset(dataset)
    summary = score_results(results, use_deepeval=os.getenv("SKIP_DEEPEVAL") != "1")
    write_json_report(results, "artifacts/eval_results.json", summary)
    write_markdown_report(results, summary, ARTIFACTS / "eval_report.md")
    return results, summary


def test_dataset_has_required_coverage():
    cases = load_goldens("evals/goldens.json")
    cats = {c.get("category") for c in cases}
    assert len([c for c in cases if c["id"].startswith("extra_")]) >= 10
    assert {"unknown_answer", "conflicting_source", "cross_account", "tool_failure", "multi_turn"} <= cats


def test_critical_cases_pass(evaluated):
    results, summary = evaluated
    assert_critical_cases_pass(results, summary)


def test_quality_gates(evaluated):
    _, summary = evaluated
    th = thresholds()
    assert summary["route_accuracy"] >= th["route_accuracy"]
    assert summary["escalation_accuracy"] >= th["escalation_accuracy"]
    assert (summary["citation_source_recall"] or 0) >= th["citation_source_recall"]
    assert summary["pass_rate"] >= th["overall_pass_rate"]


def test_report_written(evaluated):
    assert (ARTIFACTS / "eval_results.json").exists() and (ARTIFACTS / "eval_report.md").exists()
