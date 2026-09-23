# Lovable prompt

Paste everything below the line into Lovable as the first message. Then paste `frontend/api-types.ts` when Lovable asks for types, or ask it to create `src/lib/api-types.ts` with that content.

---

Build **SupportFlow Console**, a clean, professional customer-support web app for a fictional product called CloudBox. It talks to an existing FastAPI backend. Do not create a backend, database, auth, or Supabase. All data comes from the REST API described below. Use React, TypeScript, Tailwind, shadcn/ui, lucide-react icons, react-router, and TanStack Query. Use a light theme with a neutral palette and one accent color (indigo). The app must be responsive: on mobile the sidebar collapses into a sheet.

## API client
- Create `src/lib/api.ts`. The base URL comes from `localStorage["sf_api_base"]`, falling back to `import.meta.env.VITE_API_BASE_URL`, then `http://localhost:8000`. Strip any trailing slash.
- Every request sends the headers `Content-Type: application/json` and `ngrok-skip-browser-warning: true`.
- Dev-only calls also send `X-Admin-Token: localStorage["sf_admin_token"]`.
- Errors come back as JSON `{ error, detail, request_id }`. Throw an `ApiError` holding these fields, plus the HTTP status. `detail` may be a string, a list of `{loc,msg}`, or an object.
- Read the `X-Request-ID` response header and keep it available for error display.

## Global layout
- A top bar with the SupportFlow logo text, nav links (**Chat**, **Documents**, **Monitoring**), a health dot, and a Settings button.
  - The health dot calls `GET /health` every 30 s: green if `status==="ok"`, amber if `degraded`, red on network failure. Its tooltip lists `dependencies` and `llm_provider`.
- A **user switcher** (a select) in the top bar, filled from `GET /users`. Show `display_name` with a small badge for `plan` and a red "unverified" badge when `verified` is false; show "no account" when `account_id` is null. Store the selected `user_id` in localStorage (default `user_maya`). Switching users clears the open thread and reloads the thread list. Show a subtle note: "Demo identity — in production this comes from sign-in."
- A **Settings dialog** with these fields:
  - API base URL
  - admin token (password input)
  - a "Test connection" button that calls `/health` and shows each dependency status with ok/error icons.

  Settings are saved to localStorage.

## Page 1: Chat (`/` and `/t/:threadId`)
**Left sidebar:**
- A "New conversation" button, which clears the current thread; the next message creates one.
- The thread list from `GET /threads?user_id={user}`, newest first. Each item shows the title, relative time, and message count, and links to `/t/:threadId`.

**Main panel:**
- Load `GET /threads/{threadId}?user_id={user}`. If the API returns 403, show a lock state: "This conversation belongs to another user." Do not show any content.
- **Empty state:** a welcome card with 6 clickable suggestion chips that send the message immediately:
  - "What is the difference between the Standard and Team plans?"
  - "My files are duplicated after I worked offline. What should I do?"
  - "Is CloudBox currently experiencing an outage?"
  - "Can I get a refund for a renewal charge from last month?"
  - "Can you check the order ord_7001 and tell me the amount?"
  - "Does CloudBox integrate with Trello?"
- **Composer:** an auto-growing textarea (Enter sends, Shift+Enter adds a newline, max 4000 chars with a counter) and a send button. Send `POST /chat` with `{ user_id, thread_id (null for a new one), message }`.
  - Optimistically show the user bubble.
  - While waiting, show an assistant "thinking" bubble with an animated indicator. Cycle the text through "Routing request…", "Searching knowledge base…", "Checking tools…", and "Reviewing answer…".
  - After the first response of a new thread, navigate to `/t/{thread_id}` and refresh the thread list.
- **Assistant message card** (for both live responses and history messages):
  - The answer rendered as markdown. Numbered steps must render as a list.
  - A header row with badges:
    - a **route badge**, colored per route: knowledge=blue, troubleshooting=violet, account_tool=teal, status_tool=amber, escalation=red, error=gray
    - when `needs_escalation`, an **Escalated** badge with the `ticket_id` (monospace, copy button) and the `escalation.category`
    - the latency in ms
  - A **no-evidence banner** (amber, info icon) when `no_evidence` is true: "No supporting source was found, so the assistant asked a clarifying question instead of guessing."
  - A **Sources** section: horizontally scrollable cards, one per citation. Each card shows the title, `v{version}`, a `source_type` chip, and a trust chip (official=green, internal=blue, community=gray). Clicking a card expands the `snippet` and shows the score with 2 decimals.
  - A **Tool activity** collapsible, closed by default and showing a count. It holds a vertical timeline of `tool_events`. Each event shows:
    - the tool name in monospace
    - a status icon: success = green check, denied = red shield, error = amber alert
    - the `summary`, and the `error_code` as a small badge when present
    - `latency_ms`, and "retried" when attempts > 1
    - an expandable JSON view of `args`

    Denied events get a caption: "Blocked by account scope check".
  - A **Trajectory** row: small gray chips with arrows between them, built from the `trajectory` strings (for example, `orchestrator:knowledge → knowledge:answer → critic:approved → response:finalize`).
  - A **footer:**
    - 👍/👎 buttons. Clicking one calls `POST /feedback` with `{ user_id, message_id, helpful }`. 👎 first opens a small popover with an optional comment. After submitting, show a thank-you state and disable the buttons.
    - a "View trace" link (opens `trace_url` in a new tab) when `trace_url` exists
    - a copy button for `request_id`

    History messages from `GET /threads/{id}` have no trace_url, so show the `trace_id` as copyable text.
- **Error state:** if `/chat` fails, show a red inline card in place of the answer, with the error `detail` (format a validation list nicely), the `request_id`, and a Retry button that resends the same message. A 403 means "You can't post in another user's conversation"; a 404 means "Unknown user or conversation".

## Page 2: Documents (`/documents`)
- A table from `GET /documents` with columns doc_id, title, version, source_type, trust_level (colored chip), chunk_count, and indexed_at. Add filter selects for source_type and trust_level (use them as query params) and a search box that filters by title client-side.
- An **Upload** card: a drag-and-drop area accepting `.md` files. It posts multipart to `POST /documents/upload` (field name `file`) with the admin token header. Show a help text with the required metadata block:
```
---
doc_id: my_doc
title: My document
product: CloudBox
version: 3.4
source_type: user_guide
trust_level: official
---
```
  On success, show a toast with the chunk count and refresh the table. On 422, show the `detail` message inline (for example, "Missing metadata fields: version"). On 401, show "Admin token required. Set it in Settings."

## Page 3: Monitoring (`/monitoring`)
- Fetch `GET /metrics/summary?hours={h}` with an hours select (1, 24, 168). Refresh every 15 s.
- An **alerts banner** at the top: a red list of `alerts`, or a green "No active alerts".
- **KPI cards:** total requests, error rate (%), avg and p95 latency (ms), escalation rate (%), negative feedback rate (%), and total tokens.
- A **route breakdown** bar chart (recharts) from `routes`.
- A **recent requests** table: time, user, route badge, latency, status code (red if ≥500), escalated icon, tokens, an error (truncated with a tooltip), and a request_id copy button.
- A **Dev controls** card, whose calls send the admin token:
  - A status scenario segmented control (operational / previews_degraded / sync_outage) that calls `POST /dev/status-scenario?scenario=...` and shows the returned scenario.
  - A **Run evaluation** button that calls `POST /evals/run` with `{"include_extra": true, "use_deepeval": false}`, then polls `GET /evals/runs/{run_id}` every 3 s until the status is not "running". Show a result card with the status badge (passed = green, failed = red) and these summary numbers: cases, passed, pass_rate, critical_pass_rate, route_accuracy, escalation_accuracy, citation_source_recall, and failure_stages. Add a checkbox "Use DeepEval judge (slower)" that sets `use_deepeval: true`.

## States and quality
- Every list and page has loading skeletons, an empty state with a helpful message, and an error state with a retry.
- Handle network failure globally: if the API is unreachable, show a dismissible banner: "Cannot reach the SupportFlow API at {base}. Check the tunnel URL in Settings."
- Never render raw HTML from the API. Render answers with a markdown renderer that has HTML disabled.
- Keyboard accessible, with visible focus rings and aria-labels on icon buttons.
- Put all API types in `src/lib/api-types.ts` (the content will be provided).
