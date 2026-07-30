import type { Plan } from '../../api/types'

interface PlanPanelProps {
  plan: Plan | null
}

/** Renders the Planner's structured output. See docs/architecture.md section 6. */
export function PlanPanel({ plan }: PlanPanelProps) {
  // TODO(client/components/RunPanel): render `plan.steps` as an ordered list
  // (step, tool, args, rationale). Show an empty/placeholder state when
  // `plan` is null (no run selected yet).
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Plan</h3>
      <p className="mt-2 text-sm text-slate-400">
        TODO: render plan steps. {plan ? '' : '(no run selected)'}
      </p>
    </div>
  )
}
