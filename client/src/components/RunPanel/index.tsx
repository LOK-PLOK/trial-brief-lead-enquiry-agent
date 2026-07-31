import type { RunResult } from '../../api/types'
import { PlanPanel } from './PlanPanel'
import { ToolTracePanel } from './ToolTracePanel'
import { RecordPanel } from './RecordPanel'
import { VerifierPanel } from './VerifierPanel'
import { CostBadge } from './CostBadge'
import { DuplicateCheckBanner } from './DuplicateCheckBanner'

interface RunPanelProps {
  run: RunResult | null
}

/**
 * Composes the required views for a single run (trial brief section 3.8 /
 * docs/architecture.md sections 3-4): plan, tool call trace, final record,
 * verifier decision + reason, and token/cost, plus a distinct
 * duplicate-detection callout when applicable.
 */
export function RunPanel({ run }: RunPanelProps) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <DuplicateCheckBanner trace={run?.tool_call_trace ?? null} />
      <PlanPanel plan={run?.plan ?? null} />
      <ToolTracePanel trace={run?.tool_call_trace ?? null} plan={run?.plan ?? null} />
      <RecordPanel record={run?.final_record ?? null} finalStatus={run?.final_status ?? 'error'} />
      <VerifierPanel decision={run?.verifier_decision ?? null} />
      {run && (
        <CostBadge
          totalTokens={run.total_tokens}
          totalCostUsd={run.total_cost_usd}
          totalLatencyMs={run.total_latency_ms}
          llmCalls={run.llm_calls}
        />
      )}
    </div>
  )
}
