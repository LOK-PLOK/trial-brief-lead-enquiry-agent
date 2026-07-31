import type { Plan } from '../../api/types'

interface PlanPanelProps {
  plan: Plan | null
}

/**
 * Renders the Planner's validated execution plan (docs/architecture.md
 * section 6 / docs/contracts.md "Planner"): an ordered list of
 * `{step, tool, args, rationale}`, exactly as the Executor will consume it.
 */
export function PlanPanel({ plan }: PlanPanelProps) {
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Plan</h3>
      {!plan && <p className="mt-2 text-sm text-slate-400">No run selected.</p>}
      {plan && plan.steps.length === 0 && (
        <p className="mt-2 text-sm text-slate-400">Plan has no steps.</p>
      )}
      {plan && plan.steps.length > 0 && (
        <ol className="mt-2 space-y-2">
          {plan.steps.map((step) => (
            <li key={step.step} className="rounded border border-slate-100 bg-slate-50 p-2 text-sm">
              <span className="font-mono font-medium text-slate-800">
                {step.step}. {step.tool}
              </span>
              <p className="mt-1 text-xs text-slate-500">{step.rationale}</p>
              <pre className="mt-1 overflow-x-auto rounded bg-white p-2 text-xs text-slate-600">
                {JSON.stringify(step.args, null, 2)}
              </pre>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
