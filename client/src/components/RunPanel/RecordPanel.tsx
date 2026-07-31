interface RecordPanelProps {
  record: Record<string, unknown> | null
  finalStatus: string
}

const STATUS_STYLES: Record<string, string> = {
  completed: 'bg-green-100 text-green-700',
  quarantined: 'bg-amber-100 text-amber-700',
  error: 'bg-red-100 text-red-700',
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Renders the final (accepted or quarantined) lead record produced by
 * `write_record` (docs/architecture.md section 5 / docs/contracts.md
 * "Database Entities"). `finalStatus` is always shown, distinguishing
 * "completed" from "quarantined" from "error" -- a submission is never
 * silently dropped.
 */
export function RecordPanel({ record, finalStatus }: RecordPanelProps) {
  const extracted = isPlainObject(record?.extracted) ? record.extracted : null

  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">Final Record</h3>
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[finalStatus] ?? 'bg-slate-100 text-slate-600'}`}
        >
          {finalStatus}
        </span>
      </div>
      {!record && (
        <p className="mt-2 text-sm text-slate-400">
          {finalStatus === 'error'
            ? 'No record was produced (execution stopped before completion).'
            : 'No run selected.'}
        </p>
      )}
      {record && (
        <div className="mt-2 space-y-2">
          {extracted && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              {Object.entries(extracted).map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-slate-500">{key}</dt>
                  <dd className="truncate text-slate-800">{value == null ? '—' : String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {typeof record.score === 'number' && (
            <p className="text-xs text-slate-600">
              Score: <span className="font-medium text-slate-800">{record.score}</span>
            </p>
          )}
          {typeof record.dedupe_hash === 'string' && (
            <p className="truncate text-xs text-slate-400" title={record.dedupe_hash}>
              Dedupe hash: {record.dedupe_hash}
            </p>
          )}
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Raw record JSON</summary>
            <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-slate-600">
              {JSON.stringify(record, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  )
}
