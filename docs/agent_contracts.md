# Agent contracts

## Orchestrator
- **Input:** the redacted user message, `user_id`, `thread_id`, the recent turns of this thread (from `get_thread_summary`), and the server-resolved `auth` context.
- **Output (shared state):**
  - `route` and `route_reason`
  - `route_source` (`rules`, `llm`, or `rules_fallback`)
  - `escalation_category`
  - `flags` (`multi_user`, `unresolved`, `critical_block`)
  - `entities` (order, invoice, account, ticket, and workspace IDs)
  - `standalone_query` and `focus_query`
- **Rules:**
  - Hard safety rules decide first and can't be overridden by the model.
  - The model can't route to `account_tool` unless the message contains identifiers or account words.
  - "Steps didn't help" right after a troubleshooting turn escalates.
- **Model use:** at most one call (`orchestrator_classify`), for soft cases only.

## Subagent result contract
Every subagent returns a `SubagentResult` (`app/graph/state.py`), stored in `state.subagent_results`:

| Field | Type | Meaning |
| --- | --- | --- |
| `agent` | str | `knowledge`, `troubleshooting`, `account_tools`, `status`, or `escalation` |
| `status` | enum | `ok`, `no_evidence`, `denied`, `error`, `needs_info`, or `escalated` |
| `summary` | str | One-line outcome (shown in traces) |
| `evidence` | list[Evidence] | Chunks with doc_id, title, version, source_type, trust_level, content, score, and section |
| `tool_events` | list[ToolEvent] | Every tool call with status, redacted args, error code, latency, and attempts |
| `needs_escalation` | bool | Whether policy requires a human |
| `missing_information` | list[str] | What the user still needs to provide (asked in the answer) |
| `draft_answer` | str | Text for the critic to check |
| `used_doc_ids` | list[str] | Evidence the draft relies on (becomes the citations) |

## Knowledge agent (`subagents/knowledge.py`)
- **Tools:** `search_knowledge_base` (default trust official + internal, top-k 5, at most 8).
- **Behaviour:**
  - It answers only from the evidence.
  - If nothing passes the relevance floor, or the model reports `insufficient_evidence`, it answers "I couldn't find enough information…" and asks exactly one focused question (`no_evidence = true`, no citations).
  - When sources conflict, it follows the newer or more trusted source and states that an older source differs.
- **On the troubleshooting route:** it retrieves only, filtered to troubleshooting, faq, release_notes, user_guide, and internal_guide, and hands the evidence on.
- **Model use:** one call (`knowledge_answer`), falling back to extractive composition.

## Troubleshooting agent (`subagents/troubleshooting.py`)
- **Input:** evidence from the knowledge step.
- **Output:** a summary, ordered documented steps (never invented, and it never says to delete possible conflict copies before comparing them), diagnostic questions (OS, client version, one user vs workspace), and when to escalate.
- **Model use:** one call (`troubleshooting_plan`), falling back to extracting the documented list.

## Account tools agent (`subagents/account.py`)
- **Tools:** `get_order` (by order or invoice ID), `get_account`, and `get_ticket`, at most 3 per turn. It also does pinned retrieval of `agent_playbook`.
- **Behaviour:**
  - The answer is a deterministic template built from tool output, with no model call. Private data is therefore never paraphrased or mixed.
  - Each denial code has a fixed, non-leaking message.
  - If no ID is given, it asks for one (`needs_info`).

## Status check (`subagents/status.py`)
- **Tools:** `get_service_status` (the source of truth for *current* state) and pinned retrieval of `status_incidents` (policy only).
- **Behaviour:**
  - It reports components and incidents with their latest official message.
  - It never estimates a recovery time without an approved one.
  - If the tool fails, it says so and does not fall back to historical incidents.
  - If multiple users are affected, it continues to escalation.

## Escalation agent (`subagents/escalation.py`)
- **Categories:** security, credentials, privacy, legal, data_loss, billing, multi_user, outage, and unresolved.
- **Tools:**
  - pinned policy retrieval
  - for billing, a scoped `get_order` for each referenced invoice (other accounts' invoices are never read)
  - `create_ticket`
- **Ticket fields:**
  - category, priority, and a redacted summary
  - product area, steps tried, and error message
  - related IDs and thread ID
  - details: workspace ID, missing information, and the status check result
  - user ID and account ID, taken from auth
- **Answer:** it states the ticket ID, gives the policy-based next step, and asks for missing details (timestamps, error message, steps tried, invoice IDs). It never promises a refund or a time. **Model use:** one call (`escalation_reply`), falling back to category templates.

## Critic and response agent (`subagents/critic.py`)
- **Deterministic checks (always run):**
  - empty answer
  - no evidence on a knowledge or troubleshooting route
  - a knowledge answer without a citation
  - a request for a secret
  - an unsupported refund or time promise
  - identifiers or emails outside the user's scope
  - a missing ticket ID on escalations
- **LLM check (online, knowledge and troubleshooting only):** `critic_grounding` flags unsupported claims.
- **Revise:** at most one revision (`revise_answer`). In offline mode, offending sentences are removed instead.
- **Finalize:**
  - It turns `used_doc_ids` that exist in the evidence into citations and adds a "Sources:" line.
  - It adds a warning when secrets were redacted.
  - If the critic still rejects, it ships a safe fallback instead of the draft.

## Tool contracts (`app/tools/support_tools.py`)
| Tool | Arguments | Scope rule | Error codes |
| --- | --- | --- | --- |
| `search_knowledge_base` | query, filters, top_k (1–8) | Default trust official + internal | TIMEOUT, TOOL_ERROR |
| `get_account` | account_id (optional) | Must equal the session account; must be verified | NO_ACCOUNT, SCOPE_DENIED, UNVERIFIED |
| `get_order` | order_id or invoice_id, account_id | Claimed account = session; record owned by session; account verified | INVALID_ARGS, NO_ACCOUNT, SCOPE_DENIED, NOT_FOUND, UNVERIFIED, TIMEOUT |
| `get_ticket` | ticket_id | Ticket's account or user = session | SCOPE_DENIED, NOT_FOUND |
| `get_service_status` | component (optional) | Public data; mock live feed | TIMEOUT, TOOL_ERROR |
| `create_ticket` | TicketInput | User and account taken from auth; all text redacted | TIMEOUT, TOOL_ERROR |
| `get_thread_summary` | thread_id, max_turns | Thread must belong to the user | THREAD_DENIED, NOT_FOUND |

All tools run through `run_tool` (`app/tools/base.py`), which adds a thread-pool timeout, one retry for errors and timeouts (never for denials), a Langfuse `tool` observation, redacted arguments, and a `ToolEvent`.
