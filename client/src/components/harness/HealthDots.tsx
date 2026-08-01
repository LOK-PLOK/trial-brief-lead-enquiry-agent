import type { PipelineHealth } from '../../api/types'

const DOT: Record<string, string> = {
  green: 'bg-emerald-500',
  yellow: 'bg-amber-400',
  red: 'bg-rose-500',
  unknown: 'bg-slate-300',
}

const LABEL: Record<string, string> = {
  green: 'Healthy',
  yellow: 'Watch',
  red: 'Attention',
  unknown: 'Unknown',
}

export function HealthDots({ health }: { health: PipelineHealth }) {
  const items: { key: keyof PipelineHealth; label: string }[] = [
    { key: 'planner', label: 'Planner' },
    { key: 'executor', label: 'Executor' },
    { key: 'tools', label: 'Tools' },
    { key: 'verifier', label: 'Verifier' },
    { key: 'repair', label: 'Repair' },
  ]

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-3">
        {items.map(({ key, label }) => {
          const value = String(health[key] ?? 'unknown')
          return (
            <div
              key={key}
              className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700"
              title={LABEL[value] ?? value}
            >
              <span className={`inline-block h-2.5 w-2.5 rounded-full ${DOT[value] ?? DOT.unknown}`} />
              {label}
            </div>
          )
        })}
      </div>
      {health.note && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {health.note}
        </p>
      )}
    </div>
  )
}
