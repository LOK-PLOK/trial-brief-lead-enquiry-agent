import { useLeads } from '../api/hooks'
import { QueryState } from './common/QueryState'

/** Table of accepted + quarantined leads. See docs/architecture.md section 5 (LEADS). */
export function LeadsList() {
  const { data, isLoading, error } = useLeads()

  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Leads</h3>
      <QueryState isLoading={isLoading} error={error} loadingLabel="Loading leads…">
        {(!data || data.length === 0) && (
          <p className="mt-2 text-sm text-slate-400">No leads yet.</p>
        )}
        {data && data.length > 0 && (
          <table className="mt-2 w-full text-left text-xs text-slate-600">
            <thead>
              <tr>
                <th className="pr-3">Name</th>
                <th className="pr-3">Email</th>
                <th className="pr-3">Country</th>
                <th className="pr-3">Score</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((lead) => (
                <tr key={lead.id} className="border-t border-slate-100">
                  <td className="pr-3">{lead.name ?? '—'}</td>
                  <td className="pr-3">{lead.email ?? '—'}</td>
                  <td className="pr-3">{lead.country ?? '—'}</td>
                  <td className="pr-3">{lead.score ?? '—'}</td>
                  <td>
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        lead.status === 'accepted'
                          ? 'bg-green-100 text-green-700'
                          : 'bg-amber-100 text-amber-700'
                      }`}
                    >
                      {lead.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryState>
    </div>
  )
}
