# Monitoring and debugging with Langfuse

## What is recorded
Each `POST /chat` produces **one trace**. The trace name is `supportflow.chat`, `session_id` is the thread ID (so a thread becomes a Langfuse *session*), and `user_id` is the user ID. It is tagged `chat` plus the provider.

```
supportflow.chat (agent)                     input: redacted message · output: answer, route · metadata: request_id, trajectory, timings_ms, tokens
├─ tool:get_thread_summary (tool)
├─ orchestrator (agent)                      output: route, category, source=rules|llm, standalone_query
│  └─ orchestrator_classify (generation)     model, tokens, cost   ← only for soft cases
├─ knowledge_subagent (agent)
│  └─ tool:search_knowledge_base (tool)
│     └─ retrieve (retriever)                query, filters, top_k → doc_ids, scores, conflicts, insufficient
│        └─ embed_query (embedding)
│  └─ knowledge_answer (generation)
├─ critic (guardrail)                        approved, issues, llm_checked   (level WARNING when rejected)
│  └─ critic_grounding (generation)
└─ (revise (agent) → critic) …
```
The **scores** on each trace are:
- `critic_approved`, `escalated`, and `no_evidence`, added automatically
- `user_feedback`, added by `POST /feedback`
- `eval_passed` and `eval_<metric>`, added by the DeepEval runner

Each eval run also creates a `supportflow.eval_run` trace with `suite_*` scores.

**The request ID links everything together.** It appears in the `X-Request-ID` response header, in the trace metadata, in `request_logs`, and in the monitoring page. Search it in Langfuse to jump straight to the trace.

**Privacy:** messages are redacted before tracing, tool arguments are redacted in `run_tool`, and hidden prompts are never part of trace outputs.

## How to debug a failed answer (step by step)
1. **Find the trace.** Take the `trace_id` or `request_id` from the UI (the "View trace" link) or from `GET /metrics/summary` → `recent`. You can also filter Langfuse by the score `user_feedback = 0` or `critic_approved = 0`.
2. **Check routing first.** Open the `orchestrator` span. Is the `route` right? Look at `source`: `rules` means a hard rule fired, `llm` means it came from the classifier. A wrong route is a **routing** failure. Fix it in `app/graph/router.py` (the rules) or `CLASSIFIER_SYSTEM`, then add a golden case.
3. **Check retrieval.** Open the `retrieve` span. Were the expected `doc_id`s returned, with what scores? Was `insufficient` true? Were `conflicts` flagged? Missing documents point to a **retrieval** failure. Possible fixes:
   - tuning `MIN_RELEVANCE_SCORE` or `RETRIEVAL_TOP_K`
   - adjusting chunking
   - adding a filter
   - adding a missing document
4. **Check tools.** Open each `tool:*` span. Look at the status, `error_code`, attempts, and latency. `TIMEOUT` or `TOOL_ERROR` is a **tool** failure, and `SCOPE_DENIED` or `UNVERIFIED` is expected behaviour, not a bug.
5. **Check the answer.** Open the `*_answer` generation. Did the model use the evidence? Then open `critic`: which `issues` were raised, and did a revise happen? An answer that is unsupported despite good evidence is a **response** failure. Fix the prompt in `app/graph/prompts.py` or add a critic rule.
6. **Check cost and latency.** The root span's metadata has `timings_ms` (retrieval, embedding, tools, model) and total tokens. Generations show their cost per model.
7. **Lock it in.** Add the failing input to `evals/goldens_extra.json` and re-run `python -m evals.runner`. The report's *Failure analysis* section attributes each failure to routing, retrieval, tool, or response automatically.

## Dashboards and alerts
- **In Langfuse:**
  - latency (p50/p95) and cost per trace name
  - tokens by model
  - the distribution of `user_feedback` and `critic_approved` scores
  - traces with level ERROR or WARNING
- **In the app:** `GET /metrics/summary` (the Lovable monitoring page) computes the error rate, p95 latency, escalation rate, negative-feedback rate, routes, and tokens. It raises alerts when these cross the `ALERT_*` thresholds:
  - error rate above 10%
  - p95 latency above 15 s
  - escalation rate above 40% (often means a real incident is under way)
  - negative feedback above 30%
  - the same error 3+ times in the last 10 requests

## Screenshots to capture for the submission
1. A trace tree for a knowledge question, showing retrieval, generation tokens, and cost.
2. A trace for the cross-account attempt, showing `get_order` → `denied`.
3. A session view of a multi-turn thread.
4. The scores view (feedback and eval scores).
5. The dashboard showing latency and cost.
