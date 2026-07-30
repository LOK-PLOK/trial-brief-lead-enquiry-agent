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

export interface HarnessSummary {
  // TODO(client/api): fill in once evaluation/metrics.py's output shape
  // (docs/architecture.md section 11) is finalized.
  [metric: string]: unknown
}
