# Limitations and design decisions

## Decisions on gaps and inconsistencies in the brief
| Issue in the package | Decision |
| --- | --- |
| There is no user → account mapping, yet the brief requires user isolation. | A `users` table with four demo identities. `user_id` is the authenticated identity for the demo (no real auth). In production it would come from a JWT or session and never from the request body. |
| The fixtures include a `verified` flag, but the brief also says "never ask for OTP in chat". | Verification is out-of-band: the agent reads the flag and never collects codes. Unverified accounts (Omar) get a denial that explains they need to verify first. |
| There is no data source for the live status tool. | `data/service_status.json` is a mock live feed with three scenarios, switchable via `STATUS_SCENARIO` or `POST /dev/status-scenario`. `status_incidents.md` is used only for policy, never as current status. |
| The corpus contains no conflicting sources, but conflicts must be handled. | Added `product_catalog_v3_2.md` (an archived official 3.2 edition with different limits). Conflict detection prefers higher trust, then the newer version. |
| There is no community content, but trust filtering is required. | Added `community_tips.md` (`trust_level: community`), which is excluded by default. |
| Version formats are mixed ("3.4", "1.0", "2026-01"). | Versions are compared only within the same format (semantic or date). Across formats they are treated as not comparable, so no ordering is invented. |
| eval_016 references `inv_5014`, which does not exist. | The escalation still happens. The answer says that invoice couldn't be found and the specialist will check it. No data is invented. |
| eval_019 asks for a stored password, and the KB has no password-reset procedure. | Escalated as a credentials request. The answer never reveals passwords and doesn't invent reset steps. |
| eval_010 must not reveal an order for another account. | Ownership is checked before verification. Both failures return a denial that exposes nothing. |
| The Tavily package is listed in requirements. | Removed. Web search is not needed and must never be used for private data. |
| DeepEval needs a judge model key. | The runner uses `OPENAI_API_KEY` (or falls back to `MODEL_API_KEY`). Without a key, deterministic gates still run (the CI mode). |
| A Lovable app (hosted) can't call `localhost`. | Use an ngrok or cloudflared tunnel. CORS allows `*.lovable.app` and `*.lovableproject.com`. |
| There is no list-threads or metrics endpoint for the UI. | Added `GET /threads?user_id=`, `GET /users`, and `GET /metrics/summary`. |

## Known limitations
- **Authentication is simulated.** `user_id` in the body is trusted as the identity. This is acceptable for the demo only.
- **Offline mode is for testing only.** Its extractive answers are grammatical but blunt. Answer quality should be judged with `LLM_PROVIDER=openai`.
- **Rule-based safety routing** is conservative. A message that mentions a refund in passing (for example, "what is your refund policy for my plan?") may be escalated rather than answered. This is a deliberate trade-off toward safety, and the LLM decides soft cases.
- **The lexical index** (BM25) is rebuilt in memory from Qdrant. This suits a small KB. A large corpus would use Qdrant sparse vectors instead.
- **Ticket IDs** are sequential (`SUP-3004`…), and generation is not safe under high concurrency. Production would use a DB sequence.
- **Thread summaries** are the last three user messages, not an LLM summary. This keeps them cheap and deterministic.
- **Evals create real tickets** in the configured database. Run them against a dev database.
- **Relevance threshold:** `MIN_RELEVANCE_SCORE=0.30` is tuned for `text-embedding-3-small`. Other embedding models need retuning.
- **The mock status feed** is static per scenario. There is no automatic incident lifecycle.
- **The model call budget** of 6 per request means the online critic check can be skipped late in a long revise path. When that happens, the deterministic checks still run.
- **Follow-up retrieval ranking** (eval case `extra_007_multi_turn_followup`): for a short follow-up such as
  "And what about the Business plan?", the rewritten query can rank the plan-change rules chunk above the plan
  table, lowering DeepEval contextual precision (about 0.58) even though the answer is correct and faithful. A
  reranking step (for example a cross-encoder) would address this; it was not added because every extra model
  call is costly on the shared, rate-limited model key.
- **Rate-limited shared model key**: the Sprints LiteLLM proxy returns frequent HTTP 429s. The system waits and
  retries (`RATE_LIMIT_WAITS`), caches embeddings on disk, and paces evaluations (`EVAL_CASE_DELAY_S`), so answers
  can take 10–30 s when the proxy is busy and a full DeepEval run takes 1–2 hours.
