import { useState } from 'react'
import { useCreateRun, useRun, useRuns } from '../api/hooks'
import { RunPanel } from '../components/RunPanel'
import { HarnessSummary } from '../components/HarnessSummary'
import { AdversarialReport } from '../components/AdversarialReport'
import { LeadsList } from '../components/LeadsList'

type Tab = 'run' | 'history' | 'harness' | 'adversarial'

const TABS: { id: Tab; label: string }[] = [
  { id: 'run', label: 'Run' },
  { id: 'history', label: 'History' },
  { id: 'harness', label: 'Harness Summary' },
  { id: 'adversarial', label: 'Adversarial' },
]

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

  // TODO(client/pages/Home): once POST /api/runs works, set selectedRunId
  // from createRun's result so the RunPanel below updates immediately.
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createRun.mutate(enquiryText)
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
            <button
              type="submit"
              disabled={createRun.isPending}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {createRun.isPending ? 'Running…' : 'Run'}
            </button>
          </form>
          <RunPanel run={createRun.data ?? selectedRun.data ?? null} />
        </div>
      )}

      {activeTab === 'history' && (
        <div className="space-y-4">
          {/* TODO(client/pages/Home): render runsQuery.data as a proper
              clickable list (rows below are a placeholder), then show
              <RunPanel run={selectedRun.data ?? null} /> underneath. */}
          <ul className="text-sm text-slate-400">
            {(runsQuery.data ?? []).map((run) => (
              <li key={run.id}>
                <button type="button" onClick={() => setSelectedRunId(run.id)}>
                  TODO: {run.id}
                </button>
              </li>
            ))}
            {(runsQuery.data ?? []).length === 0 && <li>TODO: render run history.</li>}
          </ul>
          <LeadsList />
        </div>
      )}

      {activeTab === 'harness' && <HarnessSummary />}

      {activeTab === 'adversarial' && <AdversarialReport />}
    </div>
  )
}
