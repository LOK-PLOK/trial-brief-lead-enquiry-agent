import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  useCancelHarnessJob,
  useHarnessDashboard,
  useHarnessDatasets,
  useHarnessJob,
  useHarnessSummary,
  useParseHarnessInput,
  useStartHarnessJob,
} from '../api/hooks'
import { describeError } from '../api/errors'
import { QueryState } from '../components/common/QueryState'
import { HealthDots } from '../components/harness/HealthDots'
import { SimpleBars } from '../components/harness/SimpleBars'
import { HarnessRunDetail } from '../components/harness/HarnessRunDetail'
import type { HarnessParseResult, HarnessRunRow } from '../api/types'

type View = 'runner' | 'summary' | 'detail'

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a'
  return `${(value * 100).toFixed(1)}%`
}

function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a'
  return `${value.toFixed(0)} ms`
}

const STATUS_BADGE: Record<string, string> = {
  completed: 'bg-emerald-100 text-emerald-800',
  quarantined: 'bg-amber-100 text-amber-900',
  error: 'bg-rose-100 text-rose-800',
}

/**
 * Assessor-facing harness dashboard: start jobs, watch progress, review
 * summary/charts, drill into runs. No AI logic — API exposure only.
 */
export function HarnessTesting() {
  const [view, setView] = useState<View>('runner')
  const [datasetId, setDatasetId] = useState('standard')
  const [nRepeats, setNRepeats] = useState(1)
  const [customText, setCustomText] = useState(
    'E01\nPaste the first enquiry here.\n\nE02\nPaste the second enquiry here.\n',
  )
  const [uploadName, setUploadName] = useState<string | null>(null)
  const [parsePreview, setParsePreview] = useState<HarnessParseResult | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [batchId, setBatchId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [startError, setStartError] = useState<string | null>(null)

  const queryClient = useQueryClient()
  const datasetsQuery = useHarnessDatasets()
  const latestSummary = useHarnessSummary()
  const startJob = useStartHarnessJob()
  const cancelJob = useCancelHarnessJob()
  const parseInput = useParseHarnessInput()

  const selectedDataset = useMemo(
    () => datasetsQuery.data?.find((d) => d.id === datasetId),
    [datasetsQuery.data, datasetId],
  )
  const needsCustomInput = datasetId === 'custom' || datasetId === 'single'

  const jobQuery = useHarnessJob(jobId, {
    refetchInterval: jobId ? 1200 : false,
  })
  const job = jobQuery.data
  const jobInFlight = job?.status === 'queued' || job?.status === 'running'

  useEffect(() => {
    if (job?.batch_id) setBatchId(job.batch_id)
  }, [job?.batch_id])

  useEffect(() => {
    if (!job) return
    if (job.status !== 'completed' && job.status !== 'cancelled' && job.status !== 'failed') {
      return
    }
    setView('summary')
    void queryClient.invalidateQueries({ queryKey: ['harness', 'dashboard', job.batch_id] })
    void queryClient.invalidateQueries({ queryKey: ['harness', 'summary'] })
  }, [job?.status, job?.batch_id, queryClient])

  // Prefill summary with latest batch when opening the tab with no active job.
  useEffect(() => {
    if (!batchId && !jobId && latestSummary.data?.id) {
      setBatchId(latestSummary.data.id)
    }
  }, [batchId, jobId, latestSummary.data?.id])

  // Poll dashboard while the job runs — first fetch often sees 0 runs before
  // Pipeline persists anything; without refetch the Summary stays empty forever.
  const dashboardQuery = useHarnessDashboard(batchId, {
    refetchInterval: jobInFlight ? 1500 : false,
  })
  const dashboard = dashboardQuery.data

  const progressPct =
    job && job.n_total > 0 ? Math.min(100, Math.round((job.completed / job.n_total) * 100)) : 0

  const refreshPreview = async () => {
    if (!needsCustomInput || !customText.trim()) {
      setParsePreview(null)
      return
    }
    try {
      const result = await parseInput.mutateAsync({
        text: customText,
        filename: uploadName,
        mode: datasetId === 'single' ? 'single' : 'auto',
      })
      setParsePreview(result)
      setStartError(null)
    } catch (err) {
      setParsePreview(null)
      setStartError(describeError(err))
    }
  }

  useEffect(() => {
    if (!needsCustomInput) {
      setParsePreview(null)
      return
    }
    const handle = window.setTimeout(() => {
      void refreshPreview()
    }, 400)
    return () => window.clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce on text/mode only
  }, [customText, datasetId, uploadName, needsCustomInput])

  const handleFile = async (file: File | null) => {
    if (!file) return
    const lower = file.name.toLowerCase()
    const allowed = ['.txt', '.md', '.csv', '.json', '.markdown']
    if (!allowed.some((ext) => lower.endsWith(ext))) {
      setStartError('Supported uploads: .txt, .md, .csv, .json')
      return
    }
    const text = await file.text()
    setUploadName(file.name)
    setCustomText(text)
    setStartError(null)
  }

  const handleStart = async () => {
    setStartError(null)
    try {
      const payload =
        needsCustomInput
          ? {
              dataset_id: datasetId,
              n_repeats: datasetId === 'single' ? 1 : nRepeats,
              raw_text: customText,
              filename: uploadName,
            }
          : {
              dataset_id: datasetId,
              n_repeats: nRepeats,
            }
      const job = await startJob.mutateAsync(payload)
      setJobId(job.id)
      // Prefer the batch created at job start; never clear a prior batch with null.
      if (job.batch_id) setBatchId(job.batch_id)
      setView('runner')
      void queryClient.invalidateQueries({ queryKey: ['harness', 'dashboard', job.batch_id] })
    } catch (err) {
      setStartError(describeError(err))
    }
  }

  if (view === 'detail' && selectedRunId) {
    return (
      <HarnessRunDetail
        runId={selectedRunId}
        onBack={() => {
          setSelectedRunId(null)
          setView('summary')
        }}
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Harness Testing</h2>
          <p className="text-sm text-slate-500">
            Run evaluation datasets from the browser. No CLI required.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setView('runner')}
            className={`rounded-md px-3 py-1.5 text-sm ${
              view === 'runner' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700'
            }`}
          >
            Runner
          </button>
          <button
            type="button"
            onClick={() => setView('summary')}
            disabled={!batchId}
            className={`rounded-md px-3 py-1.5 text-sm disabled:opacity-40 ${
              view === 'summary' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700'
            }`}
          >
            Summary
          </button>
        </div>
      </div>

      {view === 'runner' && (
        <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
            <h3 className="text-sm font-semibold text-slate-800">1. Select dataset</h3>
            <QueryState
              isLoading={datasetsQuery.isLoading}
              error={datasetsQuery.error}
              loadingLabel="Loading datasets…"
            >
              <div className="space-y-2">
                {(datasetsQuery.data ?? []).map((dataset) => (
                  <label
                    key={dataset.id}
                    className={`flex cursor-pointer gap-3 rounded-lg border p-3 ${
                      datasetId === dataset.id
                        ? 'border-slate-900 bg-slate-50'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <input
                      type="radio"
                      name="dataset"
                      checked={datasetId === dataset.id}
                      onChange={() => setDatasetId(dataset.id)}
                      className="mt-1"
                    />
                    <span>
                      <span className="block text-sm font-medium text-slate-900">
                        {dataset.label}
                      </span>
                      <span className="block text-xs text-slate-500">{dataset.description}</span>
                      {!dataset.requires_upload && (
                        <span className="mt-1 block text-xs text-slate-400">
                          {dataset.n_enquiries} enquiries
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </QueryState>

            {datasetId !== 'single' && (
              <div>
                <h3 className="mb-2 text-sm font-semibold text-slate-800">2. Repeats</h3>
                <div className="flex gap-2">
                  {[1, 3].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setNRepeats(n)}
                      className={`rounded-md px-3 py-1.5 text-sm ${
                        nRepeats === n ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700'
                      }`}
                    >
                      {n}× {n === 1 ? '(default)' : '(full variance)'}
                    </button>
                  ))}
                </div>
                {selectedDataset && !selectedDataset.requires_upload && (
                  <p className="mt-2 text-xs text-slate-500">
                    Will execute {selectedDataset.n_enquiries * nRepeats} runs (
                    {selectedDataset.n_enquiries} × {nRepeats}).
                  </p>
                )}
                {datasetId === 'custom' && parsePreview && (
                  <p className="mt-2 text-xs text-slate-500">
                    Parsed {parsePreview.n_enquiries} enquiries × {nRepeats} ={' '}
                    {parsePreview.n_enquiries * nRepeats} runs ({parsePreview.format_detected}).
                  </p>
                )}
              </div>
            )}

            {needsCustomInput && (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-slate-800">
                  {datasetId === 'single' ? '2. Paste one enquiry' : '3. Paste or upload dataset'}
                </h3>
                <div className="flex flex-wrap items-center gap-3">
                  <label className="inline-flex cursor-pointer items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
                    Upload file
                    <input
                      type="file"
                      accept=".txt,.md,.markdown,.csv,.json,text/plain,text/markdown,text/csv,application/json"
                      className="hidden"
                      onChange={(e) => void handleFile(e.target.files?.[0] ?? null)}
                    />
                  </label>
                  <span className="text-xs text-slate-400">
                    .txt · .md · .csv · .json
                    {uploadName ? ` · loaded ${uploadName}` : ''}
                  </span>
                  {uploadName && (
                    <button
                      type="button"
                      className="text-xs text-slate-500 underline"
                      onClick={() => setUploadName(null)}
                    >
                      Clear filename
                    </button>
                  )}
                </div>
                <textarea
                  value={customText}
                  onChange={(e) => setCustomText(e.target.value)}
                  rows={datasetId === 'single' ? 8 : 12}
                  placeholder={
                    datasetId === 'single'
                      ? 'Paste a single lead enquiry…'
                      : 'Paste enquiries separated by blank lines, ---, or IDs like E01 / A01 / Enquiry 1…'
                  }
                  className="w-full rounded-lg border border-slate-300 p-3 font-mono text-xs"
                />
                {datasetId === 'custom' && (
                  <p className="text-xs text-slate-500">
                    Separators detected automatically: blank lines, horizontal rules (
                    <code>---</code>), and ID headings (<code>E01</code>, <code>A01</code>,{' '}
                    <code>Enquiry 1</code>). JSON and CSV are also accepted.
                  </p>
                )}
                {datasetId === 'single' && (
                  <p className="text-xs text-slate-500">
                    The entire paste is treated as one enquiry — separators are ignored.
                  </p>
                )}
                {parsePreview && (
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-600">
                    <div className="font-medium text-slate-800">
                      Preview · {parsePreview.n_enquiries} enquir
                      {parsePreview.n_enquiries === 1 ? 'y' : 'ies'} · format{' '}
                      {parsePreview.format_detected}
                    </div>
                    <ul className="mt-2 max-h-36 space-y-1 overflow-auto">
                      {parsePreview.enquiries.map((row) => (
                        <li key={row.id}>
                          <span className="font-medium text-slate-800">{row.id}</span>
                          {' — '}
                          {row.text.slice(0, 80)}
                          {row.text.length > 80 ? '…' : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleStart}
                disabled={
                  startJob.isPending ||
                  job?.status === 'running' ||
                  job?.status === 'queued' ||
                  (needsCustomInput && !customText.trim())
                }
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {startJob.isPending
                  ? 'Starting…'
                  : datasetId === 'single'
                    ? 'Run enquiry'
                    : 'Run harness'}
              </button>
              {job && (job.status === 'running' || job.status === 'queued') && (
                <button
                  type="button"
                  onClick={() => cancelJob.mutate(job.id)}
                  className="rounded-md border border-rose-300 px-4 py-2 text-sm text-rose-700"
                >
                  Cancel
                </button>
              )}
            </div>
            {startError && <p className="text-sm text-rose-600">{startError}</p>}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-5">
            <h3 className="text-sm font-semibold text-slate-800">Progress</h3>
            {!job && (
              <p className="mt-4 text-sm text-slate-400">
                Start a harness run to watch live progress here.
              </p>
            )}
            {job && (
              <div className="mt-4 space-y-4">
                <div>
                  <div className="mb-1 flex justify-between text-xs text-slate-500">
                    <span>Running Harness…</span>
                    <span>
                      {job.completed} / {job.n_total} Complete
                    </span>
                  </div>
                  <div className="h-3 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-slate-800 transition-all"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 p-3 text-sm">
                  <div className="text-xs uppercase tracking-wide text-slate-400">
                    Currently processing
                  </div>
                  <div className="mt-1 text-lg font-semibold text-slate-900">
                    {job.current_enquiry_id ?? (job.status === 'completed' ? 'Done' : '—')}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    Status: {job.status}
                    {job.phase ? ` · ${job.phase}` : ''}
                  </div>
                  {job.error && <p className="mt-2 text-xs text-rose-600">{job.error}</p>}
                </div>
                {job.status === 'completed' && (
                  <button
                    type="button"
                    onClick={() => setView('summary')}
                    className="text-sm font-medium text-slate-800 underline"
                  >
                    View Harness Summary →
                  </button>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      {view === 'summary' && (
        <QueryState
          isLoading={dashboardQuery.isLoading}
          error={dashboardQuery.error}
          loadingLabel="Loading harness summary…"
        >
          {!dashboard && (
            <p className="text-sm text-slate-400">No harness batch available yet. Run one first.</p>
          )}
          {dashboard && (
            <div className="space-y-5">
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h3 className="mb-3 text-sm font-semibold text-slate-800">Pipeline Health</h3>
                <HealthDots health={dashboard.pipeline_health} />
              </section>

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <StatCard title="Dataset" value={dashboard.dataset_label ?? '—'} />
                <StatCard
                  title="Duration"
                  value={
                    dashboard.duration_ms != null
                      ? `${(dashboard.duration_ms / 1000).toFixed(1)} s`
                      : '—'
                  }
                />
                <StatCard
                  title="Success rate"
                  value={pct(dashboard.statistics.success_rate)}
                />
                <StatCard
                  title="Total cost"
                  value={`$${dashboard.cost.total_cost_usd.toFixed(4)}`}
                />
              </div>

              <div className="grid gap-4 lg:grid-cols-3">
                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="text-sm font-semibold text-slate-800">Run information</h3>
                  <dl className="mt-3 space-y-1 text-sm text-slate-600">
                    <Row label="Dataset" value={dashboard.dataset_label ?? '—'} />
                    <Row label="Started" value={dashboard.started_at ?? '—'} />
                    <Row label="Finished" value={dashboard.finished_at ?? '—'} />
                    <Row
                      label="Duration"
                      value={
                        dashboard.duration_ms != null
                          ? `${(dashboard.duration_ms / 1000).toFixed(1)} s`
                          : '—'
                      }
                    />
                    <Row label="Repeats" value={String(dashboard.n_repeats ?? '—')} />
                  </dl>
                </section>
                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="text-sm font-semibold text-slate-800">Statistics</h3>
                  <dl className="mt-3 space-y-1 text-sm text-slate-600">
                    <Row label="Total" value={String(dashboard.statistics.total)} />
                    <Row label="Passed" value={String(dashboard.statistics.passed)} />
                    <Row label="Quarantined" value={String(dashboard.statistics.quarantined)} />
                    <Row label="Failed" value={String(dashboard.statistics.failed)} />
                    <Row label="Success rate" value={pct(dashboard.statistics.success_rate)} />
                  </dl>
                </section>
                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="text-sm font-semibold text-slate-800">Cost & performance</h3>
                  <dl className="mt-3 space-y-1 text-sm text-slate-600">
                    <Row label="Total tokens" value={String(dashboard.cost.total_tokens)} />
                    <Row
                      label="Avg cost / enquiry"
                      value={`$${dashboard.cost.average_cost_per_enquiry.toFixed(5)}`}
                    />
                    <Row
                      label="Average runtime"
                      value={ms(dashboard.performance.average_runtime_ms)}
                    />
                    <Row label="Fastest" value={ms(dashboard.performance.fastest_ms)} />
                    <Row label="Slowest" value={ms(dashboard.performance.slowest_ms)} />
                  </dl>
                </section>
              </div>

              <section className="rounded-xl border border-slate-200 bg-white p-4">
                <h3 className="text-sm font-semibold text-slate-800">Failure breakdown</h3>
                <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
                  {Object.entries(dashboard.failure_breakdown).map(([key, value]) => (
                    <div key={key} className="rounded-lg bg-slate-50 px-3 py-2 text-sm">
                      <div className="text-xs uppercase tracking-wide text-slate-400">
                        {key.replaceAll('_', ' ')}
                      </div>
                      <div className="font-semibold text-slate-800">{value}</div>
                    </div>
                  ))}
                </div>
              </section>

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                <SimpleBars
                  title="Pass vs Quarantined"
                  items={[
                    {
                      label: 'Passed',
                      value: dashboard.charts.pass_vs_quarantined.passed,
                      color: 'bg-emerald-500',
                    },
                    {
                      label: 'Quarantined',
                      value: dashboard.charts.pass_vs_quarantined.quarantined,
                      color: 'bg-amber-400',
                    },
                    {
                      label: 'Failed',
                      value: dashboard.charts.pass_vs_quarantined.failed,
                      color: 'bg-rose-500',
                    },
                  ]}
                />
                <SimpleBars
                  title="Failures by field"
                  items={Object.entries(dashboard.charts.failures_by_field).map(([label, value]) => ({
                    label,
                    value,
                    color: 'bg-slate-700',
                  }))}
                />
                <SimpleBars
                  title="Cost per stage"
                  items={Object.entries(dashboard.charts.cost_per_stage).map(([label, value]) => ({
                    label,
                    value: Number(value.toFixed(4)),
                    color: 'bg-indigo-500',
                  }))}
                />
              </div>

              <section className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-slate-800">Test table</h3>
                  <span className="text-xs text-slate-400">
                    {dashboard.runs.length} run{dashboard.runs.length === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-sm">
                    <thead className="text-xs uppercase tracking-wide text-slate-400">
                      <tr>
                        <th className="py-2 pr-3">ID</th>
                        <th className="py-2 pr-3">Status</th>
                        <th className="py-2 pr-3">Score</th>
                        <th className="py-2 pr-3">Repair</th>
                        <th className="py-2 pr-3">Runtime</th>
                        <th className="py-2 pr-3">Cost</th>
                        <th className="py-2 pr-3">Confidence</th>
                        <th className="py-2">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dashboard.runs.map((row) => (
                        <tr
                          key={row.id}
                          className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                          onClick={() => {
                            setSelectedRunId(row.id)
                            setView('detail')
                          }}
                        >
                          <td className="py-2 pr-3 font-medium text-slate-800">
                            {row.enquiry_id ?? row.id.slice(0, 8)}
                            {row.repeat_index != null && row.repeat_index > 1
                              ? ` · r${row.repeat_index}`
                              : ''}
                          </td>
                          <td className="py-2 pr-3">
                            <span
                              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                                STATUS_BADGE[row.final_status] ?? 'bg-slate-100 text-slate-700'
                              }`}
                            >
                              {row.final_status}
                            </span>
                          </td>
                          <td className="py-2 pr-3">{row.score ?? '—'}</td>
                          <td className="py-2 pr-3">{repairLabel(row)}</td>
                          <td className="py-2 pr-3">{ms(row.total_latency_ms)}</td>
                          <td className="py-2 pr-3">${row.total_cost_usd.toFixed(4)}</td>
                          <td className="py-2 pr-3">
                            {row.confidence == null ? '—' : row.confidence.toFixed(2)}
                          </td>
                          <td
                            className="max-w-md truncate py-2 text-xs text-slate-500"
                            title={
                              row.traceback
                                ? `${row.error_type ?? 'Error'}: ${row.error_message ?? row.reason}\n\n${row.traceback}`
                                : row.error_message || row.reason || undefined
                            }
                          >
                            {row.error_message || row.reason || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          )}
        </QueryState>
      )}
    </div>
  )
}

function StatCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-slate-400">{title}</div>
      <div className="mt-1 text-lg font-semibold text-slate-900">{value}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right font-medium text-slate-800">{value}</dd>
    </div>
  )
}

function repairLabel(row: HarnessRunRow): string {
  if (!row.repair_attempted) return '—'
  if (row.repair_succeeded) return 'yes ✓'
  return 'yes ✗'
}
