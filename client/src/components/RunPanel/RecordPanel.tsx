interface RecordPanelProps {
  record: Record<string, unknown> | null
  finalStatus: string
}

/** Renders the final (accepted or quarantined) lead record. See docs/architecture.md section 5. */
export function RecordPanel({ record, finalStatus }: RecordPanelProps) {
  // TODO(client/components/RunPanel): render the extracted fields, score,
  // and jurisdiction rule. Visually distinguish "completed" vs "quarantined"
  // (finalStatus) since a submission is never silently dropped.
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Final Record</h3>
      <p className="mt-2 text-sm text-slate-400">
        TODO: render record fields. Status: {finalStatus}. {record ? '' : '(no run selected)'}
      </p>
    </div>
  )
}
