import { useLeads } from '../../api/hooks'

/** Table of accepted + quarantined leads. See docs/architecture.md section 5 (LEADS). */
export function LeadsList() {
  const { data, isLoading } = useLeads()

  // TODO(client/components/LeadsList): render as a table (name, email,
  // country, score, status). Distinguish "accepted" vs "quarantined" rows
  // clearly, and link each row back to its source run.
  if (isLoading) return <p className="text-sm text-slate-400">Loading leads…</p>

  return (
    <pre className="rounded-lg border border-slate-200 p-4 text-xs text-slate-500">
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}
