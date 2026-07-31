import type { VerifierDecision } from '../../api/types'

interface VerifierPanelProps {
  decision: VerifierDecision | null
}

/**
 * Renders the independent Verifier's decision (docs/architecture.md
 * section 9 / docs/contracts.md "Verifier"): PASS/FAIL, confidence,
 * fabrication + plan-deviation detection, and the required non-empty
 * reason. The pass/fail badge is the most visually prominent element here
 * since it is the core claim the whole trial-brief pipeline is built to
 * demonstrate honestly.
 */
export function VerifierPanel({ decision }: VerifierPanelProps) {
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Verifier Decision</h3>
      {!decision && (
        <p className="mt-2 text-sm text-slate-400">No verifier decision for this run.</p>
      )}
      {decision && (
        <div className="mt-2 space-y-2">
          <div className="flex items-center gap-2">
            <span
              className={`rounded px-2 py-0.5 text-xs font-semibold ${
                decision.pass ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
              }`}
            >
              {decision.pass ? 'PASS' : 'FAIL'}
            </span>
            <span className="text-xs text-slate-500">
              confidence {(decision.confidence * 100).toFixed(0)}%
            </span>
          </div>
          <p className="text-xs text-slate-700">{decision.reason}</p>
          <ul className="space-y-1 text-xs text-slate-600">
            <li>
              Fabrication:{' '}
              <strong className={decision.fabrication_detected ? 'text-red-700' : 'text-slate-700'}>
                {decision.fabrication_detected ? 'detected' : 'none'}
              </strong>
              {decision.fabrication_detected && decision.fabricated_fields.length > 0 && (
                <> — fields: {decision.fabricated_fields.join(', ')}</>
              )}
            </li>
            <li>
              Plan deviation:{' '}
              <strong
                className={decision.plan_deviation_detected ? 'text-red-700' : 'text-slate-700'}
              >
                {decision.plan_deviation_detected ? 'detected' : 'none'}
              </strong>
              {decision.plan_deviation_detected && decision.deviation_details && (
                <> — {decision.deviation_details}</>
              )}
            </li>
          </ul>
        </div>
      )}
    </div>
  )
}
