import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Home } from './Home'
import { renderWithQueryClient } from '../test/queryClient'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * The "Run" tab button and the "Run" submit button both have the
 * accessible name "Run"; disambiguate by their (distinct) `type` attribute
 * rather than relying on DOM order.
 */
function getSubmitButton(): HTMLElement {
  const candidate = screen
    .getAllByRole('button', { name: 'Run' })
    .find((el) => el.getAttribute('type') === 'submit')
  if (!candidate) throw new Error('submit button not found')
  return candidate
}

describe('Home', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the four required tabs with Run active by default', () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Not implemented yet.' }, 501))
    renderWithQueryClient(<Home />)

    expect(screen.getAllByRole('button', { name: 'Run' }).length).toBe(2)
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Harness Testing' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Adversarial' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Paste an enquiry...')).toBeInTheDocument()
  })

  it('disables submit until the enquiry textarea has content', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Not implemented yet.' }, 501))
    const user = userEvent.setup()
    renderWithQueryClient(<Home />)

    expect(getSubmitButton()).toBeDisabled()

    await user.type(screen.getByPlaceholderText('Paste an enquiry...'), 'Hi, interested in casks.')
    expect(getSubmitButton()).toBeEnabled()
  })

  it('shows a loading state while submitting, then an error state on failure', async () => {
    let resolveFetch: (value: Response) => void = () => {}
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve
        }),
    )
    const user = userEvent.setup()
    renderWithQueryClient(<Home />)

    await user.type(screen.getByPlaceholderText('Paste an enquiry...'), 'Hi, interested in casks.')
    await user.click(getSubmitButton())

    expect(screen.getByRole('button', { name: 'Running…' })).toBeInTheDocument()
    expect(screen.getByText('Running the pipeline…')).toBeInTheDocument()

    resolveFetch(jsonResponse({ detail: 'Not implemented yet.' }, 501))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Request failed (501): Not implemented yet.',
    )
  })

  it('renders the run panels once a submission succeeds', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        id: 'run-1',
        enquiry_text: 'Hi, interested in casks.',
        enquiry_id: null,
        repeat_index: null,
        is_adversarial: false,
        plan: { steps: [] },
        plan_schema_valid: true,
        tool_call_trace: { calls: [] },
        final_record: null,
        verifier_decision: null,
        repair_attempted: false,
        repair_succeeded: null,
        final_status: 'completed',
        llm_calls: [],
        total_tokens: 0,
        total_cost_usd: 0,
        total_latency_ms: 0,
        created_at: null,
      }),
    )
    const user = userEvent.setup()
    renderWithQueryClient(<Home />)

    await user.type(screen.getByPlaceholderText('Paste an enquiry...'), 'Hi, interested in casks.')
    await user.click(getSubmitButton())

    expect(await screen.findByText('Plan')).toBeInTheDocument()
    expect(screen.getByText('Tool Call Trace')).toBeInTheDocument()
    expect(screen.getByText('Final Record')).toBeInTheDocument()
  })

  it('loading an adversarial sample switches to the Run tab and fills the textarea', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Not implemented yet.' }, 501))
    const user = userEvent.setup()
    renderWithQueryClient(<Home />)

    await user.click(screen.getByRole('button', { name: 'Adversarial' }))
    const [firstLoadButton] = screen.getAllByRole('button', { name: 'Load into Run tab' })
    await user.click(firstLoadButton)

    const textarea = await screen.findByPlaceholderText<HTMLTextAreaElement>('Paste an enquiry...')
    expect(textarea.value.length).toBeGreaterThan(0)
  })

  it('shows the harness testing runner when the Harness Testing tab is selected', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/api/harness/datasets')) {
        return jsonResponse([
          {
            id: 'standard',
            label: 'Official Trial A Dataset (E01–E15)',
            description: '15 enquiries',
            n_enquiries: 15,
            default_repeats: 1,
            requires_upload: false,
          },
          {
            id: 'custom',
            label: 'Custom upload / paste',
            description: 'Upload or paste',
            n_enquiries: 0,
            default_repeats: 1,
            requires_upload: true,
          },
          {
            id: 'single',
            label: 'Single Enquiry',
            description: 'One enquiry',
            n_enquiries: 1,
            default_repeats: 1,
            requires_upload: true,
          },
        ])
      }
      if (url.includes('/api/harness/parse')) {
        return jsonResponse({
          format_detected: 'text',
          n_enquiries: 1,
          enquiries: [{ id: 'custom-01', text: 'preview' }],
        })
      }
      if (url.includes('/api/harness/summary')) {
        return jsonResponse(null)
      }
      return jsonResponse({ detail: 'Not implemented yet.' }, 501)
    })
    const user = userEvent.setup()
    renderWithQueryClient(<Home />)

    await user.click(screen.getByRole('button', { name: 'Harness Testing' }))

    expect(await screen.findByRole('button', { name: 'Run harness' })).toBeInTheDocument()
    expect(await screen.findByText('Official Trial A Dataset (E01–E15)')).toBeInTheDocument()
    expect(
      await screen.findByText(/Run evaluation datasets from the browser/),
    ).toBeInTheDocument()
  })
})
