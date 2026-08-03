import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { HarnessSummary } from './HarnessSummary'
import { renderWithQueryClient } from '../test/queryClient'
import type { HarnessSummary as HarnessSummaryType } from '../api/types'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const fullSummary: HarnessSummaryType = {
  id: 'batch-abcdef123456',
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T01:00:00Z',
  n_runs: 45,
  config_snapshot: {},
  metrics: {
    n_runs: 45,
    completion_rate: 0.9,
    quarantined_rate: 0.05,
    error_rate: 0.05,
    pass_rate: 0.9,
    fabrication_rate: 0.02,
    fabrication_caught_rate: null,
    planner_schema_breach_rate: null,
    extractor_schema_breach_rate: null,
    tool_selection_accuracy: 1,
    repair_attempted_rate: 0.05,
    repair_success_rate: 1,
    mean_latency_ms: 1234.5,
    median_latency_ms: 1100,
    p95_latency_ms: 2200,
    mean_tokens: 800,
    mean_cost_usd: 0.0012,
    variance: {
      by_enquiry: {
        'enquiry-1': {
          n_repeats: 3,
          final_statuses: ['completed', 'completed', 'completed'],
          latency_ms_stdev: 10.5,
          cost_usd_stdev: 0.00001,
          tokens_stdev: 2,
          score_stdev: 0,
          distinct_final_record_count: 1,
        },
      },
      overall_latency_ms_stdev: 12,
      overall_cost_usd_stdev: 0.00002,
      overall_tokens_stdev: 3,
    },
    notes: ['fabrication_caught_rate is not measurable from the current contract.'],
  },
}

describe('HarnessSummary', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a loading state while the request is in flight', () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}))
    renderWithQueryClient(<HarnessSummary />)
    expect(screen.getByText('Loading harness summary…')).toBeInTheDocument()
  })

  it('shows an error state when the harness summary request fails (500)', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Internal server error.' }, 500))
    renderWithQueryClient(<HarnessSummary />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Request failed (500): Internal server error.',
    )
  })

  it('renders the full metric table, variance table, and honesty notes on success', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(fullSummary))
    renderWithQueryClient(<HarnessSummary />)

    expect(await screen.findByText(/Harness batch batch-ab/)).toBeInTheDocument()
    expect(screen.getByText('Runs')).toBeInTheDocument()
    expect(screen.getAllByText('90.0%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('n/a').length).toBeGreaterThan(0)
    expect(screen.getByText('Run-to-run variance by enquiry')).toBeInTheDocument()
    expect(screen.getByText('enquiry-1')).toBeInTheDocument()
    expect(screen.getByText('Honesty notes')).toBeInTheDocument()
    expect(
      screen.getByText('fabrication_caught_rate is not measurable from the current contract.'),
    ).toBeInTheDocument()
  })

  it('always shows the duplicate-detection independence note, regardless of query state', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Internal server error.' }, 500))
    renderWithQueryClient(<HarnessSummary />)

    expect(
      await screen.findByText(/Duplicate detection is verified independently/),
    ).toBeInTheDocument()
  })
})
