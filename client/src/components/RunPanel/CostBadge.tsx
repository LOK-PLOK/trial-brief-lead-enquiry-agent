import type { LlmCallUsage } from '../../api/types'

interface CostBadgeProps {
  totalTokens: number
  totalCostUsd: number
  totalLatencyMs: number
  llmCalls?: LlmCallUsage[]
}

/**
 * Summary of token usage, cost, and latency for a run (docs/architecture.md
 * section 12, "every adapter call records usage/latency at the call site").
 * When `llmCalls` is provided, breaks the total down by stage
 * (planner/extractor/verifier/repair) using `RunResult.llm_calls`.
 */
export function CostBadge({
  totalTokens,
  totalCostUsd,
  totalLatencyMs,
  llmCalls = [],
}: CostBadgeProps) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 md:col-span-2">
      <h3 className="text-sm font-semibold text-slate-700">Token Usage &amp; Cost</h3>
      <p className="mt-2 text-sm text-slate-700">
        {totalTokens.toLocaleString()} tokens · ${totalCostUsd.toFixed(6)} ·{' '}
        {(totalLatencyMs / 1000).toFixed(2)}s
      </p>
      {llmCalls.length > 0 && (
        <table className="mt-2 w-full text-left text-xs text-slate-500">
          <thead>
            <tr>
              <th className="pr-3">Stage</th>
              <th className="pr-3">Model</th>
              <th className="pr-3">Tokens</th>
              <th className="pr-3">Cost</th>
              <th>Latency</th>
            </tr>
          </thead>
          <tbody>
            {llmCalls.map((call, i) => (
              <tr key={`${call.stage}-${i}`} className="border-t border-slate-100">
                <td className="pr-3">{call.stage}</td>
                <td className="pr-3">{call.model_name}</td>
                <td className="pr-3">{call.prompt_tokens + call.completion_tokens}</td>
                <td className="pr-3">${call.cost_usd.toFixed(6)}</td>
                <td>{call.latency_ms.toFixed(0)}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
