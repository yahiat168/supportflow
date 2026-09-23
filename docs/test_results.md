# Test results

## 1. Automated tests (offline, no keys): `pytest -v`
**67 passed**. Tests run with in-memory Qdrant, SQLite, and `LLM_PROVIDER=offline`. They are pinned in `tests/conftest.py`, so a local `.env` never changes them.

| Brief scenario | Test | Result |
| --- | --- | --- |
| 1. Product question with ≥2 citations | `test_product_question_two_citations` | ✅ |
| 2. Troubleshooting with more than one agent step | `test_troubleshooting_multi_step` (orchestrator → knowledge retrieve → troubleshooting → critic) | ✅ |
| 3. Unknown answer + one focused question | `test_unknown_answer` | ✅ |
| 4. Cross-account order denied | `test_cross_account_denied` (+ `test_own_order_allowed_with_scoped_args`) | ✅ |
| 5. Security issue creates a ticket | `test_security_escalation_creates_ticket` | ✅ |
| 6. Status question uses the status tool | `test_status_uses_tool` | ✅ |
| 7. Tool timeout, no crash | `test_tool_timeout_no_crash` (TIMEOUT after 2 attempts) | ✅ |
| 8. Persistence after restart | `test_persistence_after_restart` | ✅ |
| 9. User isolation for threads | `test_user_isolation` (403 on read, 403 on chat, not listed) | ✅ |
| 10. Feedback stored | `test_feedback` (+ owner check, shows in metrics) | ✅ |
| 11. New document updates retrieval | `test_document_ingestion_updates_retrieval` (+ bad metadata → 422) | ✅ |
| 12. Evaluation endpoint protected | `test_eval_endpoint_protected` | ✅ |

Additional tests cover the following:
- **RAG:**
  - metadata validation
  - chunking: rule plus steps kept together, tables kept whole, a table kept with its notes paragraph, FAQ pairs kept intact
  - mixed-format versions
  - trust and source_type filters
  - conflict detection, with superseded sources ranked last
  - the relevance window
  - the RetrievedChunk contract
- **Safety:** routing rules for all 20 goldens, redaction, every scope error code, timeout and retry, no retry on denials, the top-k bound, critic guardrails, and graph shape.
- **Online LLM paths (stubbed model):** structured output wiring, hallucinated doc_ids dropped, fallback on model failure, critic rejection triggering a revision, and the call budget.

## 2. Final evaluation: real model + DeepEval judge
**Configuration:**
- Model `gemini/gemini-3.6-flash` through the Sprints LiteLLM proxy.
- Embeddings `gemini/gemini-embedding-001`.
- The DeepEval judge is the same model.
- Command: `python -m evals.runner`.
- Reports: `artifacts/final/eval_results.json` and `artifacts/final/eval_report.md`.

35 cases (20 original goldens + 15 new): **32 passed (91.4%)**, with no judge errors.

| Metric | Result | Threshold |
| --- | --- | --- |
| Pass rate | **91.4%** (32/35) | ≥ 85% ✅ |
| Critical pass rate (21 safety/privacy/escalation/tool cases) | **100%** | 100% ✅ |
| Route accuracy | **100%** | ≥ 90% ✅ |
| Escalation accuracy | **100%** | ≥ 95% ✅ |
| Citation source recall | 0.985 | ≥ 0.70 ✅ |
| Faithfulness (mean) | **1.000** | ≥ 0.70 ✅ |
| Contextual recall (mean) | **1.000** | ≥ 0.60 ✅ |
| Contextual precision (mean) | 0.968 | ≥ 0.60 ✅ |
| Answer relevancy (mean) | 0.947 | ≥ 0.70 ✅ |
| Answer points coverage, GEval (mean) | 0.943 | ≥ 0.60 ✅ |
| Task completion, GEval (mean) | 0.994 | ≥ 0.60 ✅ |
| DeepEval metric errors | 0 | 0 ✅ |

### Remaining failures (all non-critical, stage: response)
| Case | Metric | Analysis |
| --- | --- | --- |
| eval_001 (Standard vs Team) | answer_relevancy 0.667 | The answer is complete and faithful. The judge penalised the extra note about the archived 3.2 catalogue and the closing offer of help. The same case scored 0.83–0.89 in earlier runs, so this is a borderline judge variation. The conflict note is kept on purpose, for transparency about differing sources. |
| eval_006 (current outage?) | answer_points_coverage 0.3 | **Judge misreading.** The trajectory and tool events show that `get_service_status` *was* called and the answer comes from it. The judge objected to the cited policy document's title ("…incident history"), which is cited for the *policy* that the status tool is the source of truth. The answer-points metric does not see tool calls. Possible improvement: omit this policy citation from the customer-facing "Sources:" line on status answers. |
| extra_007 (follow-up "what about Business?") | contextual_precision 0.583 | Known limitation (see `docs/limitations.md`): for a short follow-up, the plan-change rules chunk can rank above the plan table. The answer is correct and faithful. It passed (precision 1.0) in the previous targeted run, so the result depends on how the follow-up is rewritten. A reranker would fix it, at the cost of extra model calls on a rate-limited key. |

### How the results improved during development
| Run | Pass rate | Change that made the difference |
| --- | --- | --- |
| First real-model run (no judge) | 94% (33/35) | — |
| Fixed: routing of "why…" questions; a search timeout under 429s; one Langfuse trace per case | 35/35 on the targeted re-run | Routing and retrieval fixes |
| First full judged run | 80% (28/35) | Contextual precision was low (0.42–0.58) on plan questions |
| Superseded sources ranked last, tables embedded as sentences, tables kept with their notes | Precision 0.58 → 0.97 (mean) | Retrieval ranking fixes |
| Order answers state verification; internal playbook hidden from the customer-facing Sources line; answers scoped to the question; escalation includes the Trash self-service step; judge given chunk metadata | **91.4% (32/35)** | Answer-quality fixes |

## 3. Offline evaluation (CI, deterministic gates)
`python -m evals.runner --no-deepeval` in offline mode gives **35/35**, with all gates passing. The same command runs in `.github/workflows/ci.yml`.
