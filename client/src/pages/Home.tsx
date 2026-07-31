import { useState } from 'react'
import { useCreateRun, useRun, useRuns } from '../api/hooks'
import { RunPanel } from '../components/RunPanel'
import { HarnessSummary } from '../components/HarnessSummary'
import { AdversarialReport } from '../components/AdversarialReport'
import { LeadsList } from '../components/LeadsList'
import { QueryState } from '../components/common/QueryState'
import { describeError } from '../api/errors'
import type { FinalStatus } from '../api/types'

type Tab = 'run' | 'history' | 'harness' | 'adversarial'

const TABS: { id: Tab; label: string }[] = [
  { id: 'run', label: 'Run' },
  { id: 'history', label: 'History' },
  { id: 'harness', label: 'Harness Summary' },
  { id: 'adversarial', label: 'Adversarial' },
]

const STATUS_BADGE: Record<FinalStatus, string> = {
  completed: 'bg-green-100 text-green-700',
  quarantined: 'bg-amber-100 text-amber-700',
  error: 'bg-red-100 text-red-700',
}

/**
 * The single page required by the brief (docs/architecture.md section 4):
 * internal tab state, no router. Deliberately plain — "functional rather
 * than designed".
 */
export function Home() {
  const [activeTab, setActiveTab] = useState<Tab>('run')
  const [enquiryText, setEnquiryText] = useState('')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const createRun = useCreateRun()
  const runsQuery = useRuns()
  const selectedRun = useRun(selectedRunId)

  // The most recently created run takes priority over a selected history
  // row; selecting a history row resets the mutation so its (now stale)
  // data stops shadowing the freshly-selected run.
  const displayedRun = createRun.data ?? selectedRun.data ?? null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!enquiryText.trim() || createRun.isPending) return
    createRun.mutate(enquiryText, {
      onSuccess: (result) => setSelectedRunId(result.id),
    })
  }

  const handleSelectHistoryRun = (runId: string) => {
    createRun.reset()
    setSelectedRunId(runId)
  }

  const handleLoadAdversarialEnquiry = (text: string) => {
    createRun.reset()
    setEnquiryText(text)
    setActiveTab('run')
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">Lead Enquiry Agent</h1>
        <p className="text-sm text-slate-500">
          Planner → Executor → independent Verifier, with a one-shot repair loop.
        </p>
      </header>

      <nav className="mb-6 flex gap-2 border-b border-slate-200">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-2 text-sm font-medium ${
              activeTab === tab.id
                ? 'border-b-2 border-slate-900 text-slate-900'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {activeTab === 'run' && (
        <div className="space-y-4">
          <form onSubmit={handleSubmit} className="space-y-2">
            <textarea
              value={enquiryText}
              onChange={(e) => setEnquiryText(e.target.value)}
              placeholder="Paste an enquiry..."
              rows={4}
              className="w-full rounded-lg border border-slate-300 p-3 text-sm"
            />
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={createRun.isPending || !enquiryText.trim()}
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {createRun.isPending ? 'Running…' : 'Run'}
              </button>
              {createRun.isPending && (
                <span className="text-xs text-slate-400">Running the pipeline…</span>
              )}
            </div>
          </form>

          {createRun.isError && (
            <p
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
            >
              {describeError(createRun.error)}
            </p>
          )}

          <RunPanel run={displayedRun} />
        </div>
      )}

      {activeTab === 'history' && (
        <div className="space-y-4">
          <QueryState
            isLoading={runsQuery.isLoading}
            error={runsQuery.error}
            loadingLabel="Loading run history…"
          >
            {(!runsQuery.data || runsQuery.data.length === 0) && (
              <p className="text-sm text-slate-400">No runs yet.</p>
            )}
            {runsQuery.data && runsQuery.data.length > 0 && (
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {runsQuery.data.map((run) => (
                  <li key={run.id}>
                    <button
                      type="button"
                      onClick={() => handleSelectHistoryRun(run.id)}
                      className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50 ${
                        selectedRunId === run.id ? 'bg-slate-50' : ''
                      }`}
                    >
                      <span className="font-mono text-xs text-slate-500">{run.id.slice(0, 8)}</span>
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[run.final_status]}`}
                      >
                        {run.final_status}
                      </span>
                      <span className="text-xs text-slate-400">
                        ${run.total_cost_usd.toFixed(6)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>

          {selectedRunId && !createRun.data && (
            <QueryState
              isLoading={selectedRun.isLoading}
              error={selectedRun.error}
              loadingLabel="Loading run…"
            >
              <RunPanel run={selectedRun.data ?? null} />
            </QueryState>
          )}

          <LeadsList />
        </div>
      )}

      {activeTab === 'harness' && <HarnessSummary />}

      {activeTab === 'adversarial' && (
        <AdversarialReport onLoadEnquiry={handleLoadAdversarialEnquiry} />
      )}
    </div>
  )
}
