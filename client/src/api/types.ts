/**
 * Mirrors server/app/schemas/*.py. Keep these in sync by hand for now;
 * TODO(client/api): consider generating this file from the FastAPI OpenAPI
 * schema (e.g. via `openapi-typescript`) once the API stabilizes, so the two
 * can never drift.
 */

export type ToolName = 'parse_enquiry' | 'lookup_jurisdiction_rule' | 'score_lead' | 'write_record'

export interface PlanStep {
  step: number
  tool: ToolName
  args: Record<string, unknown>
  rationale: string
}

export interface Plan {
  steps: PlanStep[]
}

export interface ToolCall {
  step: number
  tool: ToolName
  args: Record<string, unknown>
  status: 'success' | 'error'
  result: Record<string, unknown> | null
  error: string | null
  latency_ms: number
}

export interface ToolCallTrace {
  calls: ToolCall[]
}

export interface VerifierDecision {
  pass: boolean
  confidence: number
  fabrication_detected: boolean
  fabricated_fields: string[]
  plan_deviation_detected: boolean
  deviation_details: string | null
  reason: string
}

export interface LlmCallUsage {
  stage: string
  model_provider: string
  model_name: string
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  latency_ms: number
}

export type FinalStatus = 'completed' | 'quarantined' | 'error'

export interface RunResult {
  id: string
  enquiry_text: string
  enquiry_id: string | null
  repeat_index: number | null
  is_adversarial: boolean
  plan: Plan | null
  plan_schema_valid: boolean
  tool_call_trace: ToolCallTrace | null
  final_record: Record<string, unknown> | null
  verifier_decision: VerifierDecision | null
  repair_attempted: boolean
  repair_succeeded: boolean | null
  final_status: FinalStatus
  llm_calls: LlmCallUsage[]
  total_tokens: number
  total_cost_usd: number
  total_latency_ms: number
  created_at: string | null
}

export interface RunSummary {
  id: string
  enquiry_id: string | null
  final_status: FinalStatus
  verifier_pass: boolean | null
  total_cost_usd: number
  total_latency_ms: number
  created_at: string | null
}

/**
 * Mirrors evaluation/metrics.py's `compute_harness_metrics()` output. Rate
 * fields are `null` when the metric is architecturally unmeasurable (see
 * `notes`) rather than a fabricated number -- render `null` as "n/a", not 0.
 */
export interface HarnessVarianceByEnquiry {
  n_repeats: number
  final_statuses: string[]
  latency_ms_stdev: number
  cost_usd_stdev: number
  tokens_stdev: number
  score_stdev: number | null
  distinct_final_record_count: number
}

export interface HarnessVariance {
  by_enquiry: Record<string, HarnessVarianceByEnquiry>
  overall_latency_ms_stdev: number
  overall_cost_usd_stdev: number
  overall_tokens_stdev: number
}

export interface HarnessMetrics {
  n_runs: number
  completion_rate: number | null
  quarantined_rate: number | null
  error_rate: number | null
  pass_rate: number | null
  fabrication_rate: number | null
  fabrication_caught_rate: number | null
  planner_schema_breach_rate: number | null
  extractor_schema_breach_rate: number | null
  tool_selection_accuracy: number | null
  repair_attempted_rate: number | null
  repair_success_rate: number | null
  mean_latency_ms: number
  median_latency_ms: number
  p95_latency_ms: number
  mean_tokens: number
  mean_cost_usd: number
  variance: HarnessVariance
  notes: string[]
}

/** Mirrors the `harness_batches` row returned by GET /api/harness/summary. */
export interface HarnessSummary {
  id: string
  started_at: string | null
  finished_at: string | null
  n_runs: number
  metrics: HarnessMetrics | null
  config_snapshot: Record<string, unknown> | null
}

export type LeadStatus = 'accepted' | 'quarantined'

/** Mirrors the `leads` table. See docs/contracts.md "Database Entities". */
export interface Lead {
  id: string
  dedupe_hash: string
  name: string | null
  email: string | null
  phone: string | null
  country: string | null
  budget_band: string | null
  asset_interest: string | null
  urgency: string | null
  score: number | null
  score_breakdown: Record<string, unknown> | null
  jurisdiction_rule: Record<string, unknown> | null
  status: LeadStatus
  source_run_id: string | null
  created_at: string | null
}
