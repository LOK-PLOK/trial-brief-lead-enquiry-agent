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
  return useQuery({ queryKey: ['runs'], queryFn: api.listRuns, retry: false })
}

export function useRun(runId: string | null) {
  return useQuery({
    queryKey: ['runs', runId],
    queryFn: () => api.getRun(runId!),
    enabled: runId !== null,
    retry: false,
  })
}

export function useHarnessSummary() {
  return useQuery({
    queryKey: ['harness', 'summary'],
    queryFn: api.getHarnessSummary,
    retry: false,
  })
}

export function useHarnessDatasets() {
  return useQuery({
    queryKey: ['harness', 'datasets'],
    queryFn: api.listHarnessDatasets,
    retry: false,
  })
}

export function useHarnessJob(jobId: string | null, opts?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: ['harness', 'jobs', jobId],
    queryFn: () => api.getHarnessJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: opts?.refetchInterval ?? false,
    retry: false,
  })
}

export function useStartHarnessJob() {
  return useMutation({ mutationFn: api.startHarnessJob })
}

export function useParseHarnessInput() {
  return useMutation({ mutationFn: api.parseHarnessInput })
}

export function useCancelHarnessJob() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.cancelHarnessJob,
    onSuccess: (job) => {
      queryClient.setQueryData(['harness', 'jobs', job.id], job)
    },
  })
}

export function useHarnessDashboard(
  batchId: string | null,
  opts?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: ['harness', 'dashboard', batchId],
    queryFn: () => api.getHarnessDashboard(batchId!),
    enabled: batchId !== null,
    refetchInterval: opts?.refetchInterval ?? false,
    retry: false,
  })
}

export function useRunTimeline(runId: string | null) {
  return useQuery({
    queryKey: ['harness', 'timeline', runId],
    queryFn: () => api.getRunTimeline(runId!),
    enabled: runId !== null,
    retry: false,
  })
}

export function useLeads(status?: 'accepted' | 'quarantined') {
  return useQuery({
    queryKey: ['leads', status],
    queryFn: () => api.listLeads(status),
    retry: false,
  })
}
