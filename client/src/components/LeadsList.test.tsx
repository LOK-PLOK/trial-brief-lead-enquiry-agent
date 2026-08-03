import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { LeadsList } from './LeadsList'
import { renderWithQueryClient } from '../test/queryClient'
import type { Lead } from '../api/types'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const leads: Lead[] = [
  {
    id: 'lead-1',
    dedupe_hash: 'hash-1',
    name: 'Alex Tan',
    email: 'alex@example.com',
    phone: '+65 5551234',
    country: 'Singapore',
    budget_band: 'C',
    asset_interest: 'Whisky cask',
    urgency: 'Immediate',
    score: 80,
    score_breakdown: {},
    jurisdiction_rule: {},
    status: 'accepted',
    source_run_id: 'run-1',
    created_at: '2026-01-01T00:00:00Z',
  },
]

describe('LeadsList', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a loading state while fetching', () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}))
    renderWithQueryClient(<LeadsList />)
    expect(screen.getByText('Loading leads…')).toBeInTheDocument()
  })

  it('shows an error state when the request fails', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Not implemented yet.' }, 501))
    renderWithQueryClient(<LeadsList />)
    expect(await screen.findByRole('alert')).toHaveTextContent('501')
  })

  it('shows an empty state when there are no leads', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]))
    renderWithQueryClient(<LeadsList />)
    expect(await screen.findByText('No leads yet.')).toBeInTheDocument()
  })

  it('renders each lead with its status badge', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(leads))
    renderWithQueryClient(<LeadsList />)

    expect(await screen.findByText('Alex Tan')).toBeInTheDocument()
    expect(screen.getByText('alex@example.com')).toBeInTheDocument()
    expect(screen.getByText('accepted')).toBeInTheDocument()
  })
})
