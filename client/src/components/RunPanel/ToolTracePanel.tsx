import type { Plan, ToolCallTrace } from '../../api/types'

interface ToolTracePanelProps {
  trace: ToolCallTrace | null
  plan?: Plan | null
}

/**
 * Renders the Executor's actual tool call trace (docs/architecture.md
 * section 7 / docs/contracts.md "Executor"). When `plan` is supplied, each
 * call is cross-checked against the tool the Plan specified for that step
 * and flagged if it differs -- the same plan-vs-trace deviation the
 * Verifier independently checks, made visible for manual inspection.
 */
export function ToolTracePanel({ trace, plan = null }: ToolTracePanelProps) {
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Tool Call Trace</h3>
      {!trace && <p className="mt-2 text-sm text-slate-400">No run selected.</p>}
      {trace && trace.calls.length === 0 && (
        <p className="mt-2 text-sm text-slate-400">No tools were called.</p>
      )}
      {trace && trace.calls.length > 0 && (
        <ol className="mt-2 space-y-2">
          {trace.calls.map((call) => {
            const plannedTool = plan?.steps.find((step) => step.step === call.step)?.tool
            const deviated = plannedTool !== undefined && plannedTool !== call.tool
            return (
              <li
                key={call.step}
                className="rounded border border-slate-100 bg-slate-50 p-2 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-medium text-slate-800">
                    {call.step}. {call.tool}
                  </span>
                  <span
                    className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${
                      call.status === 'success'
                        ? 'bg-green-100 text-green-700'
                        : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {call.status} · {call.latency_ms.toFixed(0)}ms
                  </span>
                </div>
                {deviated && (
                  <p className="mt-1 text-xs font-medium text-amber-600">
                    Plan deviation: plan specified &quot;{plannedTool}&quot; at this step.
                  </p>
                )}
                {call.error && <p className="mt-1 text-xs text-red-600">Error: {call.error}</p>}
                {call.result !== null && (
                  <pre className="mt-1 overflow-x-auto rounded bg-white p-2 text-xs text-slate-600">
                    {JSON.stringify(call.result, null, 2)}
                  </pre>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
