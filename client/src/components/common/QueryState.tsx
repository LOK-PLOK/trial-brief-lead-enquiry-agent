import type { ReactNode } from 'react'
import { describeError } from '../../api/errors'

interface QueryStateProps {
  isLoading: boolean
  error: unknown
  loadingLabel?: string
  children: ReactNode
}

/**
 * Shared loading/error rendering for TanStack Query results (requirement:
 * "Show loading and error states"). Renders `children` only once loading
 * has finished and no error occurred, so callers can assume happy-path data
 * is present.
 */
export function QueryState({
  isLoading,
  error,
  loadingLabel = 'Loading…',
  children,
}: QueryStateProps) {
  if (isLoading) {
    return <p className="text-sm text-slate-400">{loadingLabel}</p>
  }
  if (error) {
    return (
      <p
        role="alert"
        className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
      >
        {describeError(error)}
      </p>
    )
  }
  return <>{children}</>
}
