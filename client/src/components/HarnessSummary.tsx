import { useHarnessSummary } from '../api/hooks'

/**
 * Summary view of the 45-run evaluation harness. See docs/architecture.md
 * section 11 for the exact metrics this must surface: completion rate,
 * schema breach rates, fabrication rate + verifier catch proportion, tool
 * selection accuracy, repair success rate, mean tokens/cost/latency, and
 * run-to-run variance.
 */
export function HarnessSummary() {
  const { data, isLoading, error } = useHarnessSummary()

  // TODO(client/components/HarnessSummary): render each metric from
  // docs/architecture.md section 11 explicitly (don't just dump JSON) once
  // GET /api/harness/summary returns real data. A simple table is fine per
  // the brief's "functional rather than designed" guidance.
  if (isLoading) return <p className="text-sm text-slate-400">Loading harness summary…</p>
  if (error)
    return <p className="text-sm text-red-500">TODO: no harness run yet, or failed to load.</p>

  return (
    <pre className="rounded-lg border border-slate-200 p-4 text-xs text-slate-500">
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}
