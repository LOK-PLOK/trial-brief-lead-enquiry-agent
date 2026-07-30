/**
 * TanStack Query hooks wrapping api/client.ts. Presentational components
 * (components/, pages/) should use these rather than calling the API client
 * directly, per docs/architecture.md section 4.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from './client'

export function useCreateRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createRun,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

export function useRuns() {
  return useQuery({ queryKey: ['runs'], queryFn: api.listRuns })
}

export function useRun(runId: string | null) {
  return useQuery({
    queryKey: ['runs', runId],
    queryFn: () => api.getRun(runId!),
    enabled: runId !== null,
  })
}

export function useHarnessSummary() {
  return useQuery({ queryKey: ['harness', 'summary'], queryFn: api.getHarnessSummary })
}

export function useLeads(status?: 'accepted' | 'quarantined') {
  return useQuery({ queryKey: ['leads', status], queryFn: () => api.listLeads(status) })
}
