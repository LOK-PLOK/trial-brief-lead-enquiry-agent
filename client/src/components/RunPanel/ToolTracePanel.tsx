import type { ToolCallTrace } from '../../api/types'

interface ToolTracePanelProps {
  trace: ToolCallTrace | null
}

/** Renders the Executor's actual tool call trace. See docs/architecture.md section 7. */
export function ToolTracePanel({ trace }: ToolTracePanelProps) {
  // TODO(client/components/RunPanel): render `trace.calls` (step, tool,
  // args, status, result/error, latency_ms). Consider visually diffing
  // against the Plan (e.g. highlight substituted/skipped tools) since that
  // is exactly what the Verifier's plan-deviation check looks for.
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Tool Call Trace</h3>
      <p className="mt-2 text-sm text-slate-400">
        TODO: render tool calls. {trace ? '' : '(no run selected)'}
      </p>
    </div>
  )
}
