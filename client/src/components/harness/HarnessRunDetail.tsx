import { useState } from 'react'
import { useRun } from '../../api/hooks'
import { QueryState } from '../common/QueryState'
import { PlanPanel } from '../RunPanel/PlanPanel'
import { ToolTracePanel } from '../RunPanel/ToolTracePanel'
import { RecordPanel } from '../RunPanel/RecordPanel'
import { VerifierPanel } from '../RunPanel/VerifierPanel'
import { CostBadge } from '../RunPanel/CostBadge'
import { RunTimeline } from './RunTimeline'

type DetailTab =
  | 'overview'
  | 'planner'
  | 'trace'
  | 'record'
  | 'verifier'
  | 'repair'
  | 'timeline'
  | 'raw'

const TABS: { id: DetailTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'planner', label: 'Planner' },
  { id: 'trace', label: 'Tool Trace' },
  { id: 'record', label: 'Final Record' },
  { id: 'verifier', label: 'Verifier' },
  { id: 'repair', label: 'Repair' },
  { id: 'raw', label: 'Raw JSON' },
]

export function HarnessRunDetail({
  runId,
  onBack,
}: {
  runId: string
  onBack: () => void
}) {
  const [tab, setTab] = useState<DetailTab>('overview')
  const { data: run, isLoading, error } = useRun(runId)

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={onBack}
        className="text-sm font-medium text-slate-600 hover:text-slate-900"
      >
        ← Back to summary
      </button>

      <QueryState isLoading={isLoading} error={error} loadingLabel="Loading run…">
        {run && (
          <>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h2 className="text-lg font-semibold text-slate-900">
                {run.enquiry_id ?? run.id.slice(0, 8)}
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Status <span className="font-medium text-slate-800">{run.final_status}</span>
                {' · '}
                {run.total_latency_ms.toFixed(0)} ms · ${run.total_cost_usd.toFixed(4)}
              </p>
            </div>

            <div className="flex flex-wrap gap-1 border-b border-slate-200 pb-1">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={`rounded-md px-3 py-1.5 text-sm ${
                    tab === t.id
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-600 hover:bg-slate-100'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {tab === 'overview' && (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
                  <h3 className="font-semibold text-slate-800">Enquiry</h3>
                  <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-600">
                    {run.enquiry_text}
                  </pre>
                </div>
                <CostBadge
                  totalTokens={run.total_tokens}
                  totalCostUsd={run.total_cost_usd}
                  totalLatencyMs={run.total_latency_ms}
                  llmCalls={run.llm_calls}
                />
                {(run.error_message || run.error_type) && (
                  <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm md:col-span-2">
                    <h3 className="font-semibold text-rose-900">Error</h3>
                    <p className="mt-1 font-mono text-xs text-rose-800">
                      {run.error_type ?? 'Error'}: {run.error_message}
                    </p>
                    {run.traceback && (
                      <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-rose-950/90 p-3 text-[11px] text-rose-50">
                        {run.traceback}
                      </pre>
                    )}
                  </div>
                )}
                <VerifierPanel decision={run.verifier_decision} />
                <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
                  <h3 className="font-semibold text-slate-800">Repair</h3>
                  <p className="mt-2 text-slate-600">
                    {run.repair_attempted
                      ? run.repair_succeeded
                        ? 'Repair attempted and succeeded.'
                        : 'Repair attempted but did not clear the failure.'
                      : 'Repair was not needed for this run.'}
                  </p>
                </div>
              </div>
            )}

            {tab === 'timeline' && <RunTimeline runId={runId} />}
            {tab === 'planner' && <PlanPanel plan={run.plan} />}
            {tab === 'trace' && (
              <ToolTracePanel trace={run.tool_call_trace} plan={run.plan} />
            )}
            {tab === 'record' && (
              <RecordPanel record={run.final_record} finalStatus={run.final_status} />
            )}
            {tab === 'verifier' && <VerifierPanel decision={run.verifier_decision} />}
            {tab === 'repair' && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-700">
                <p>
                  <strong>Attempted:</strong> {String(run.repair_attempted)}
                </p>
                <p className="mt-1">
                  <strong>Succeeded:</strong>{' '}
                  {run.repair_succeeded === null ? 'n/a' : String(run.repair_succeeded)}
                </p>
                <p className="mt-3 text-slate-500">
                  Repair re-runs extractor/tools then re-verifies. Full stage costs appear in
                  Overview → cost breakdown.
                </p>
              </div>
            )}
            {tab === 'raw' && (
              <pre className="max-h-[32rem] overflow-auto rounded-xl border border-slate-200 bg-slate-950 p-4 text-xs text-slate-100">
                {JSON.stringify(run, null, 2)}
              </pre>
            )}
          </>
        )}
      </QueryState>
    </div>
  )
}
