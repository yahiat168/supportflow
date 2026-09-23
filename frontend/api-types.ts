// TypeScript mirror of app/api/schemas.py. Paste into src/lib/api-types.ts in Lovable.
export type Route = "knowledge" | "troubleshooting" | "account_tool" | "escalation" | "status_tool" | "error";

export interface Citation {
  doc_id: string; title: string; version: string; source_type: string;
  trust_level: "official" | "internal" | "community" | string; snippet: string; score: number;
}
export interface ToolEvent {
  tool: string; status: "success" | "error" | "denied"; args: Record<string, unknown>;
  summary: string; error_code: string | null; latency_ms: number; attempts: number;
}
export interface Escalation { needs_escalation: boolean; category: string | null; ticket_id: string | null; priority: string | null; }
export interface ChatResponse {
  thread_id: string; answer: string; citations: Citation[]; tool_events: ToolEvent[]; needs_escalation: boolean;
  message_id: string; route: Route; escalation: Escalation; ticket_id: string | null; no_evidence: boolean;
  missing_information: string[]; trajectory: string[]; request_id: string; trace_id: string | null;
  trace_url: string | null; latency_ms: number;
}
export interface ThreadOut { thread_id: string; user_id: string; title: string; summary: string; created_at: string; updated_at: string; message_count: number; }
export interface MessageOut {
  message_id: string; role: "user" | "assistant"; content: string; route: Route | null; citations: Citation[];
  tool_events: ToolEvent[]; needs_escalation: boolean; ticket_id: string | null; trace_id: string | null; created_at: string;
}
export interface ThreadDetail extends ThreadOut { messages: MessageOut[]; }
export interface UserOut { user_id: string; display_name: string; account_id: string | null; plan: string | null; verified: boolean; }
export interface DocumentOut { doc_id: string; title: string; product: string; version: string; source_type: string; trust_level: string; filename: string; chunk_count: number; indexed_at: string; }
export interface HealthOut { status: "ok" | "degraded"; version: string; environment: string; llm_provider: string; dependencies: Record<string, string>; indexed_documents: number; indexed_chunks: number; }
export interface MetricsSummary {
  window: string; total_requests: number; error_count: number; error_rate: number; avg_latency_ms: number;
  p95_latency_ms: number; escalation_rate: number; negative_feedback_rate: number; routes: Record<string, number>;
  total_tokens: number; alerts: string[];
  recent: { request_id: string; user_id: string | null; route: string | null; latency_ms: number; status_code: number; error: string | null; needs_escalation: boolean; tokens: number; trace_id: string | null; created_at: string }[];
}
export interface EvalRunOut { run_id: string; status: "running" | "passed" | "failed" | "error"; summary: Record<string, any>; report_path: string | null; }
export interface ApiError { error: string; detail: string | { loc: (string | number)[]; msg: string }[] | Record<string, unknown>; request_id: string | null; }
