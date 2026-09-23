# API contract

The base URL is `http://localhost:8000`. Interactive docs are at `/docs` and the schema is at `/openapi.json`.

Every response carries an `X-Request-ID` header. You can send your own, and it is propagated. The same ID is stored with the Langfuse trace and the `request_logs` row.

## Error format
Every error has the same shape. Hidden prompts, stack traces, secrets, and other users' data are never returned.
```json
{"error": "forbidden", "detail": "This thread belongs to another user.", "request_id": "req_66b27828e5c94d76"}
```
| Status | `error` | When |
| --- | --- | --- |
| 401 | unauthorized | Missing or wrong `X-Admin-Token` on a protected route |
| 403 | forbidden | Thread or feedback owned by another user; dev route in production |
| 404 | not_found | Unknown user, thread, message, or eval run |
| 422 | invalid_input | Pydantic validation failure (the `detail` is a list of `{loc, msg}`), or bad document metadata |
| 500 | internal_error | Unexpected error (logged with the request ID) |
| 503 | dependency_unavailable | Vector DB, embeddings, or ticket system unavailable |

## Endpoints

### `GET /health`
```json
{"status": "ok", "version": "1.0.0", "environment": "development", "llm_provider": "openai",
 "dependencies": {"database": "ok", "vector_db": "ok", "langfuse": "ok", "model": "configured"},
 "indexed_documents": 15, "indexed_chunks": 51}
```
`status` is `degraded` when the database or vector DB is down, or when the model key is missing.

### `POST /chat`
Request:
```json
{"user_id": "user_maya", "thread_id": null, "message": "I was charged twice for inv_5011 and inv_5014"}
```
`thread_id: null` creates a new thread. The optional header `X-Fault-Inject: get_order:timeout,get_service_status:error` works outside production only.

Response (`ChatResponse`). The first five fields are the brief's required contract.
```json
{
  "thread_id": "thr_0999a09b1d6e",
  "answer": "A duplicate charge needs review by a billing specialist ... I've created escalation ticket SUP-3004. ...\n\nSources: CloudBox billing and refunds policy (v2026-01); CloudBox support escalation policy (v2026-02)",
  "citations": [{"doc_id": "billing_refunds", "title": "CloudBox billing and refunds policy", "version": "2026-01",
                 "source_type": "billing_policy", "trust_level": "official", "snippet": "Duplicate charges ...", "score": 0.61}],
  "tool_events": [
    {"tool": "get_thread_summary", "status": "success", "args": {"thread_id": "thr_...", "max_turns": 6}, "summary": "0 previous messages", "error_code": null, "latency_ms": 3, "attempts": 1},
    {"tool": "search_knowledge_base", "status": "success", "args": {"query": "...", "filters": {"doc_id": ["billing_refunds", "escalation_policy"]}}, "summary": "4 chunks from [...]", "latency_ms": 180, "attempts": 1},
    {"tool": "get_order", "status": "success", "args": {"invoice_id": "inv_5011", "account_id": "acc_1001"}, "summary": "ord_7001 / inv_5011: 29 USD, paid", "latency_ms": 4, "attempts": 1},
    {"tool": "get_order", "status": "error", "args": {"invoice_id": "inv_5014", "account_id": "acc_1001"}, "summary": "No record found for inv_5014.", "error_code": "NOT_FOUND", "latency_ms": 3, "attempts": 1},
    {"tool": "create_ticket", "status": "success", "args": {"ticket": {"category": "billing", "...": "..."}}, "summary": "Created SUP-3004 (billing, normal)", "latency_ms": 6, "attempts": 1}
  ],
  "needs_escalation": true,
  "message_id": "msg_1c2d...",
  "route": "escalation",
  "escalation": {"needs_escalation": true, "category": "billing", "ticket_id": "SUP-3004", "priority": "normal"},
  "ticket_id": "SUP-3004",
  "no_evidence": false,
  "missing_information": [],
  "trajectory": ["orchestrator:escalation", "escalation:billing", "critic:approved", "response:finalize"],
  "request_id": "req_66b27828e5c94d76",
  "trace_id": "2ec35e55b86f58a476d29124390a92ad",
  "trace_url": "https://cloud.langfuse.com/project/.../traces/2ec35e...",
  "latency_ms": 2140
}
```
`route` is one of `knowledge`, `troubleshooting`, `account_tool`, `escalation`, `status_tool`, or `error`.

A tool event's `status` is one of:
- `success`
- `error`, with `error_code` `TIMEOUT`, `TOOL_ERROR`, `NOT_FOUND`, or `INVALID_ARGS`
- `denied`, with `error_code` `SCOPE_DENIED`, `UNVERIFIED`, `NO_ACCOUNT`, or `THREAD_DENIED`

The chat endpoint returns 403 if the thread belongs to another user, and 404 if the user or thread is unknown.

### `POST /threads` → 201
Request `{"user_id": "user_maya", "title": "Billing question"}`. Returns a `ThreadOut`: `thread_id`, `user_id`, `title`, `summary`, `created_at`, `updated_at`, `message_count`.

### `GET /threads?user_id=user_maya`
Lists that user's threads, newest first. This is an addition used by the thread switcher.

### `GET /threads/{thread_id}?user_id=user_maya`
Returns a `ThreadDetail`: the thread summary plus its `messages` (role, content, route, citations, tool_events, needs_escalation, ticket_id, trace_id, created_at). If the caller doesn't own the thread, it returns **403**.

### `POST /documents` → 201
Request `{"filename": "linux_beta.md", "content": "---\ndoc_id: ...\n---\n# ..."}`. The content must start with the full metadata block. Re-ingesting a `doc_id` replaces its chunks.

`POST /documents/upload` does the same with a multipart `.md` file. Both return a `DocumentOut`: doc_id, title, product, version, source_type, trust_level, filename, chunk_count, indexed_at. Both require `X-Admin-Token` when `EVALS_ADMIN_TOKEN` is set.

### `GET /documents?source_type=&trust_level=`
Lists indexed documents with their metadata and chunk counts.

### `POST /feedback` → 201
Request `{"user_id": "user_maya", "message_id": "msg_...", "helpful": false, "comment": "Missing detail"}`. The feedback is stored and attached to the message's Langfuse trace as the `user_feedback` score (0 or 1). Only the thread owner can rate a message (403 otherwise).

### `POST /tickets` → 201
Request:
```json
{"user_id": "user_maya", "category": "billing", "summary": "Charged twice", "product_area": "billing",
 "steps_tried": [], "error_message": null, "related_ids": ["inv_5011"], "thread_id": null, "priority": "normal"}
```
Returns `{"ticket_id": "SUP-3005", "category": "billing", "priority": "normal", "status": "open", "tool_event": {...}}`. This endpoint uses the same controlled `create_ticket` tool as the agent, so all text fields are redacted.

### `POST /evals/run` → 202 (protected)
Requires the `X-Admin-Token` header. The route is disabled when `APP_ENV=production`.

Request `{"include_extra": true, "case_ids": null, "use_deepeval": true}` returns `{"run_id": "evr_...", "status": "running"}`. Poll `GET /evals/runs/{run_id}` (same token) until the status is `passed`, `failed`, or `error`. The summary then includes the gates, and the report is at `artifacts/eval_results.json`.

### `GET /users`
Lists the demo identities (for the user switcher), with account ID, plan, and verified flag.

### `GET /metrics/summary?hours=24&recent=20`
Returns `total_requests`, `error_count`, `error_rate`, `avg_latency_ms`, `p95_latency_ms`, `escalation_rate`, `negative_feedback_rate`, `routes`, `total_tokens`, `alerts`, and `recent`. Alert thresholds come from the `ALERT_*` environment variables. Alerts cover:
- the error rate
- p95 latency
- the escalation rate (a possible-incident signal)
- negative feedback
- a repeated identical error

### `POST /dev/status-scenario?scenario=operational|previews_degraded|sync_outage` (protected)
Switches the mock live status feed for the outage part of the demo.
