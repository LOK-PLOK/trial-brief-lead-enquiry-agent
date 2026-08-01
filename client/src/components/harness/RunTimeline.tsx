import { useRunTimeline } from '../../api/hooks'
import { QueryState } from '../common/QueryState'

const STATUS_STYLES: Record<string, string> = {
  ok: 'border-emerald-300 bg-emerald-50 text-emerald-800',
  warn: 'border-amber-300 bg-amber-50 text-amber-900',
  error: 'border-rose-300 bg-rose-50 text-rose-900',
}

export function RunTimeline({ runId }: { runId: string }) {
  const { data, isLoading, error } = useRunTimeline(runId)

  return (
    <QueryState isLoading={isLoading} error={error} loadingLabel="Loading timeline…">
      <ol className="space-y-3">
        {(data ?? []).map((event, index) => (
          <li key={`${event.stage}-${index}`} className="flex gap-3">
            <div className="flex w-6 flex-col items-center">
              <span
                className={`mt-1 h-2.5 w-2.5 rounded-full ${
                  event.status === 'ok'
                    ? 'bg-emerald-500'
                    : event.status === 'warn'
                      ? 'bg-amber-400'
                      : 'bg-rose-500'
                }`}
              />
              {index < (data?.length ?? 0) - 1 && <span className="mt-1 w-px flex-1 bg-slate-200" />}
            </div>
            <div
              className={`flex-1 rounded-lg border px-3 py-2 text-sm ${
                STATUS_STYLES[event.status] ?? 'border-slate-200 bg-slate-50 text-slate-700'
              }`}
            >
              <div className="font-medium">{event.label}</div>
              {event.detail && <div className="mt-0.5 text-xs opacity-80">{event.detail}</div>}
            </div>
          </li>
        ))}
      </ol>
    </QueryState>
  )
}
