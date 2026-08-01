/**
 * Thin typed fetch client for the FastAPI JSON API. See docs/contracts.md
 * section 7 ("API Endpoints") and docs/architecture.md section 3.
 *
 * Relative paths (`/api/...`) work unmodified both in dev (proxied by Vite,
 * see vite.config.ts) and in production (same-origin, single container).
 */

import type {
  HarnessDashboard,
  HarnessDataset,
  HarnessJob,
  HarnessParseResult,
  HarnessSummary,
  Lead,
  RunResult,
  RunSummary,
  TimelineEvent,
} from './types'

/**
 * Raised whenever the API responds with a non-2xx status. `detail` carries
 * the parsed JSON error body when the response was JSON (FastAPI's
 * exception handlers return `{"detail": "..."}` per docs/contracts.md
 * section 3), otherwise the raw response text.
 */
export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!res.ok) {
    const bodyText = await res.text()
    let detail: unknown = bodyText
    let message = bodyText || res.statusText || `Request failed with status ${res.status}`
    try {
      const parsed: unknown = bodyText ? JSON.parse(bodyText) : undefined
      if (parsed !== undefined) {
        detail = parsed
        if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
          const parsedDetail = (parsed as { detail: unknown }).detail
          message = typeof parsedDetail === 'string' ? parsedDetail : JSON.stringify(parsedDetail)
        }
      }
    } catch {
      // Body wasn't JSON; fall back to the raw text already assigned above.
    }
    throw new ApiError(res.status, message, detail)
  }

  if (res.status === 204) {
    return undefined as T
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

export function listHarnessDatasets(): Promise<HarnessDataset[]> {
  return request<HarnessDataset[]>('/api/harness/datasets')
}

export function parseHarnessInput(body: {
  text?: string
  filename?: string | null
  mode?: 'auto' | 'single'
  enquiries?: unknown
}): Promise<HarnessParseResult> {
  return request<HarnessParseResult>('/api/harness/parse', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function startHarnessJob(body: {
  dataset_id: string
  n_repeats: number
  enquiries?: unknown
  raw_text?: string
  filename?: string | null
}): Promise<HarnessJob> {
  return request<HarnessJob>('/api/harness/jobs', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getHarnessJob(jobId: string): Promise<HarnessJob> {
  return request<HarnessJob>(`/api/harness/jobs/${jobId}`)
}

export function cancelHarnessJob(jobId: string): Promise<HarnessJob> {
  return request<HarnessJob>(`/api/harness/jobs/${jobId}/cancel`, { method: 'POST' })
}

export function getHarnessDashboard(batchId: string): Promise<HarnessDashboard> {
  return request<HarnessDashboard>(`/api/harness/batches/${batchId}/dashboard`)
}

export function getRunTimeline(runId: string): Promise<TimelineEvent[]> {
  return request<TimelineEvent[]>(`/api/harness/runs/${runId}/timeline`)
}

export function listLeads(status?: 'accepted' | 'quarantined'): Promise<Lead[]> {
  const qs = status ? `?status=${status}` : ''
  return request<Lead[]>(`/api/leads${qs}`)
}
