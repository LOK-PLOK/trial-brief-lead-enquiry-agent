import type { RunResult } from '../../api/types'
import { PlanPanel } from './PlanPanel'
import { ToolTracePanel } from './ToolTracePanel'
import { RecordPanel } from './RecordPanel'
import { VerifierPanel } from './VerifierPanel'
import { CostBadge } from './CostBadge'

interface RunPanelProps {
  run: RunResult | null
}

/**
 * Composes the four required views for a single run (docs/architecture.md
 * section 3.8 / this repo's section 3-4): plan, tool call trace, final
 * record, verifier decision + reason, plus token cost.
 */
export function RunPanel({ run }: RunPanelProps) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <PlanPanel plan={run?.plan ?? null} />
      <ToolTracePanel trace={run?.tool_call_trace ?? null} />
      <RecordPanel record={run?.final_record ?? null} finalStatus={run?.final_status ?? 'error'} />
      <VerifierPanel decision={run?.verifier_decision ?? null} />
      {run && (
        <CostBadge
          totalTokens={run.total_tokens}
          totalCostUsd={run.total_cost_usd}
          totalLatencyMs={run.total_latency_ms}
        />
      )}
    </div>
  )
}
