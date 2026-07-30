import type { VerifierDecision } from '../../api/types'

interface VerifierPanelProps {
  decision: VerifierDecision | null
}

/** Renders the independent Verifier's decision + reason. See docs/architecture.md section 9. */
export function VerifierPanel({ decision }: VerifierPanelProps) {
  // TODO(client/components/RunPanel): render pass/fail, confidence,
  // fabrication_detected + fabricated_fields, plan_deviation_detected +
  // deviation_details, and reason. Make the pass/fail state visually
  // unambiguous (this is the core of what's being demonstrated).
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Verifier Decision</h3>
      <p className="mt-2 text-sm text-slate-400">
        TODO: render verifier decision + reason. {decision ? '' : '(no run selected)'}
      </p>
    </div>
  )
}
