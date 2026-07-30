interface CostBadgeProps {
  totalTokens: number
  totalCostUsd: number
  totalLatencyMs: number
}

/** Small summary badge for token/cost/latency. See docs/architecture.md section 3, "the token cost". */
export function CostBadge({ totalTokens, totalCostUsd, totalLatencyMs }: CostBadgeProps) {
  // TODO(client/components/RunPanel): format nicely (e.g. "$0.0012 - 842
  // tokens - 1.4s"). Consider a breakdown by stage (planner/extractor/
  // verifier/repair) using `RunResult.llm_calls`.
  return (
    <div className="text-xs text-slate-500">
      TODO: {totalTokens} tokens / ${totalCostUsd.toFixed(4)} / {totalLatencyMs}ms
    </div>
  )
}
