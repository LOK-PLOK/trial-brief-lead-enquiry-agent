/**
 * Thin typed fetch client for the FastAPI JSON API. See docs/api.md and
 * docs/architecture.md section 3.
 *
 * Relative paths (`/api/...`) work unmodified both in dev (proxied by Vite,
 * see vite.config.ts) and in production (same-origin, single container).
 */

import type { HarnessSummary, RunResult, RunSummary } from './types'

class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    // TODO(client/api): parse the structured error body (docs/architecture.md
    // section 3's exception handlers) instead of just the status text.
    throw new ApiError(res.status, await res.text())
  }
  return res.json() as Promise<T>
}

export function createRun(enquiryText: string): Promise<RunResult> {
  return request<RunResult>('/api/runs', {
    method: 'POST',
    body: JSON.stringify({ enquiry_text: enquiryText }),
  })
}

export function listRuns(): Promise<RunSummary[]> {
  return request<RunSummary[]>('/api/runs')
}

export function getRun(runId: string): Promise<RunResult> {
  return request<RunResult>(`/api/runs/${runId}`)
}

export function getHarnessSummary(): Promise<HarnessSummary> {
  return request<HarnessSummary>('/api/harness/summary')
}

export function listLeads(status?: 'accepted' | 'quarantined') {
  const qs = status ? `?status=${status}` : ''
  return request(`/api/leads${qs}`)
}
