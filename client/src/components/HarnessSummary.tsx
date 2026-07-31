import { useHarnessSummary } from '../api/hooks'
import { QueryState } from './common/QueryState'
import type { HarnessMetrics } from '../api/types'

function pct(value: number | null): string {
  return value === null ? 'n/a' : `${(value * 100).toFixed(1)}%`
}

function num(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 })
}

const METRIC_ROWS: { label: string; render: (m: HarnessMetrics) => string }[] = [
  { label: 'Runs', render: (m) => String(m.n_runs) },
  { label: 'Completion rate', render: (m) => pct(m.completion_rate) },
  { label: 'Quarantined rate', render: (m) => pct(m.quarantined_rate) },
  { label: 'Error rate', render: (m) => pct(m.error_rate) },
  { label: 'Verifier pass rate', render: (m) => pct(m.pass_rate) },
  { label: 'Fabrication rate (flagged)', render: (m) => pct(m.fabrication_rate) },
  { label: 'Fabrication caught rate', render: (m) => pct(m.fabrication_caught_rate) },
  { label: 'Planner schema breach rate', render: (m) => pct(m.planner_schema_breach_rate) },
  { label: 'Extractor schema breach rate', render: (m) => pct(m.extractor_schema_breach_rate) },
  { label: 'Tool selection accuracy', render: (m) => pct(m.tool_selection_accuracy) },
  { label: 'Repair attempted rate', render: (m) => pct(m.repair_attempted_rate) },
  { label: 'Repair success rate', render: (m) => pct(m.repair_success_rate) },
  { label: 'Mean latency', render: (m) => `${num(m.mean_latency_ms)} ms` },
  { label: 'Median latency', render: (m) => `${num(m.median_latency_ms)} ms` },
  { label: 'p95 latency', render: (m) => `${num(m.p95_latency_ms)} ms` },
  { label: 'Mean tokens', render: (m) => num(m.mean_tokens) },
  { label: 'Mean cost', render: (m) => `$${m.mean_cost_usd.toFixed(6)}` },
]

/**
 * Summary view of the 45-run evaluation harness (docs/architecture.md
 * section 11 / trial brief section 3.6). Reads the latest persisted
 * `harness_batches` row via GET /api/harness/summary -- the harness itself
 * runs out-of-band as a CLI script, never triggered from here.
 */
export function HarnessSummary() {
  const { data, isLoading, error } = useHarnessSummary()
  const metrics = data?.metrics ?? null

  return (
    <div className="space-y-4">
      <QueryState isLoading={isLoading} error={error} loadingLabel="Loading harness summary…">
        {!data && <p className="text-sm text-slate-400">No harness batch has been run yet.</p>}
        {data && (
          <>
            <div className="rounded-lg border border-slate-200 p-4">
              <h3 className="text-sm font-semibold text-slate-700">
                Harness batch {data.id.slice(0, 8)}
              </h3>
              <p className="text-xs text-slate-500">
                {data.n_runs} runs · started {data.started_at ?? 'n/a'} · finished{' '}
                {data.finished_at ?? 'in progress'}
              </p>
              {metrics && (
                <table className="mt-3 w-full text-left text-sm">
                  <tbody>
                    {METRIC_ROWS.map((row) => (
                      <tr key={row.label} className="border-t border-slate-100">
                        <td className="py-1 pr-4 text-slate-500">{row.label}</td>
                        <td className="py-1 font-medium text-slate-800">{row.render(metrics)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {!metrics && (
                <p className="mt-2 text-sm text-slate-400">
                  This batch has not finished yet; metrics will appear once it completes.
                </p>
              )}
            </div>

            {metrics && Object.keys(metrics.variance.by_enquiry).length > 0 && (
              <div className="rounded-lg border border-slate-200 p-4">
                <h3 className="text-sm font-semibold text-slate-700">
                  Run-to-run variance by enquiry
                </h3>
                <table className="mt-2 w-full text-left text-xs text-slate-600">
                  <thead>
                    <tr>
                      <th className="pr-3">Enquiry</th>
                      <th className="pr-3">Repeats</th>
                      <th className="pr-3">Statuses</th>
                      <th className="pr-3">Latency stdev</th>
                      <th className="pr-3">Cost stdev</th>
                      <th>Distinct records</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(metrics.variance.by_enquiry).map(([enquiryId, v]) => (
                      <tr key={enquiryId} className="border-t border-slate-100">
                        <td className="pr-3">{enquiryId}</td>
                        <td className="pr-3">{v.n_repeats}</td>
                        <td className="pr-3">{v.final_statuses.join(', ')}</td>
                        <td className="pr-3">{num(v.latency_ms_stdev)} ms</td>
                        <td className="pr-3">${v.cost_usd_stdev.toFixed(6)}</td>
                        <td>{v.distinct_final_record_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {metrics && metrics.notes.length > 0 && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <h3 className="text-sm font-semibold text-slate-700">Honesty notes</h3>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-600">
                  {metrics.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </QueryState>

      <div className="rounded-lg border border-dashed border-slate-300 p-4 text-xs text-slate-500">
        Duplicate detection is verified independently of this 45-run harness — see the dedicated
        pipeline integration test, run separately via pytest, never folded into these numbers.
        Submit the same enquiry twice from the Run tab to observe it live.
      </div>
    </div>
  )
}
