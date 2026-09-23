# Evaluation suite

| File | Purpose |
| --- | --- |
| `goldens.json` | The 20 original cases (unchanged) |
| `case_context.json` | Run context for the originals: signed-in user, status scenario, critical flag, extra must/must-not checks |
| `goldens_extra.json` | 15 new cases: unknown answer, conflicting sources, cross-account (×4), tool failures (×2), multi-turn (×2), active incident, secret redaction, trust filter, prompt injection, data loss |
| `goldens_known_failures.json` | One deliberate failing example for the demo (`--with-known-failures`) |
| `thresholds.json` | Quality gates used by CI and `POST /evals/run` |
| `runner.py` | Runs every case through the real graph, scores it, attributes failures, and writes reports and Langfuse scores |
| `test_supportflow_goldens.py` | pytest / `deepeval test run` entrypoint using the brief's function names |

**Metrics.** Every case gets the following:
- Deterministic checks:
  - route
  - escalation
  - ticket stated
  - citation and retrieval source recall
  - tool arguments and status
  - no secret requests
  - no unsupported promises
  - account isolation
  - must/must-not contain
  - no secret persisted
  - bounded execution
- DeepEval checks (with a judge key):
  - AnswerRelevancy
  - GEval answer-points coverage
  - GEval task completion (with tools_called)

RAG-route cases also get Faithfulness, ContextualPrecision, and ContextualRecall.

**Failure attribution:** each failed case is labelled `routing`, `retrieval`, `tool`, `response`, or `crash` in `artifacts/eval_report.md`.
