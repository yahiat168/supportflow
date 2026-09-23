# Demo script (7–10 minutes)

**Preparation:**
- Start the API with `LLM_PROVIDER=openai`, Langfuse keys, and `EVALS_ADMIN_TOKEN` set.
- Start the tunnel (`ngrok http 8000`) and open the Lovable app.
- Log into Langfuse in a second tab.
- Run `python -m evals.runner --with-known-failures` beforehand, so the report is ready.
- Reset the status feed: `POST /dev/status-scenario?scenario=operational`.

| # | Time | Action (as the brief asks) | What to point out | Rubric |
| --- | --- | --- | --- | --- |
| 1 | 0:00–0:45 | Introduce SupportFlow and show the Lovable UI (user switcher, thread list, chat, documents, monitoring). | The architecture diagram in the README; identity comes from the user switcher, never from chat text. | Lovable |
| 2 | 0:45–1:45 | As **Maya**, ask: *"What is the difference between the Standard and Team plans?"* Then ask the follow-up *"And what about Business?"* | Source cards (title, version, trust), the "Sources:" line, the conflict note about the archived 3.2 catalogue, and thread memory. | RAG, LangGraph |
| 3 | 1:45–2:45 | Ask: *"My files are duplicated after I worked offline. What should I do?"* | Tool activity and trajectory: orchestrator → knowledge retrieve → troubleshooting → critic. Ordered steps and diagnostic questions. | LangGraph |
| 4 | 2:45–3:30 | Switch the status feed to `previews_degraded`, then ask *"Is CloudBox currently experiencing an outage?"* | `get_service_status` in tool activity; the incident ID and latest message; no invented recovery time. | Tools |
| 5 | 3:30–4:15 | Ask: *"Can I get a refund for a renewal charge from last month?"* | The escalation badge with ticket ID SUP-…, the billing category, and "can't promise a refund". Optionally show the ticket row in Postgres. | Tools, safety |
| 6 | 4:15–5:00 | Switch to **Omar** and ask: *"My account ID is acc_1002. Show me the order ord_7001."* Then open Maya's thread URL as Nour to show the 403. | `get_order` shows as **denied / SCOPE_DENIED**, and no data is leaked. Thread isolation. | Tool safety |
| 7 | 5:00–6:30 | Open **Langfuse** from the "View trace" link. | The trace tree (orchestrator, retrieve with doc_ids and scores, tool spans, generations with tokens and cost), latency, the session view of the multi-turn thread, and the scores (`critic_approved`, `user_feedback`). Click 👎 on an answer first to show the feedback score. | Langfuse |
| 8 | 6:30–7:45 | Show `artifacts/eval_report.md` (or run `python -m evals.runner --cases eval_004 extra_003_cross_account_invoice known_001_kb_gap`). | Gates, DeepEval metrics, a **passing** case, and the **failing** `known_001_kb_gap` with its failure stage and reason. Mention CI (`.github/workflows/ci.yml`). | DeepEval |
| 9 | 7:45–8:30 | Open `/docs` and try `POST /chat` or `GET /health`. | Pydantic models, the structured error (for example, `/threads/{id}` with the wrong user returns 403 JSON), and the `X-Request-ID` header. | FastAPI |
| 10 | 8:30–9:30 | Show the repository: README, `tests/` (run `pytest -q` → 64 passed), `.env.example`, and `docs/`. | No secrets in the repo; limitations documented. | Tests/docs |

**Backup demo for a tool failure (optional):** in `/docs`, send `POST /chat` with the header `X-Fault-Inject: get_order:timeout`. The response shows the `TIMEOUT` tool event after 2 attempts, with no crash.
