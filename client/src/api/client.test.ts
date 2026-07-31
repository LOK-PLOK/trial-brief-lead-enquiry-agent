import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createRun, getHarnessSummary, getRun, listLeads, listRuns } from './client'

function jsonResponse(body: unknown, init: { status?: number } = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api/client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('createRun posts the enquiry text and returns the parsed RunResult', async () => {
    const fakeRun = { id: 'run-1', enquiry_text: 'hello' }
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(fakeRun))

    const result = await createRun('hello')

    expect(result).toEqual(fakeRun)
    expect(fetch).toHaveBeenCalledWith(
      '/api/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ enquiry_text: 'hello' }),
      }),
    )
  })

  it('listRuns GETs /api/runs', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([{ id: 'run-1' }]))

    const result = await listRuns()

    expect(result).toEqual([{ id: 'run-1' }])
    expect(fetch).toHaveBeenCalledWith('/api/runs', expect.anything())
  })

  it('getRun GETs /api/runs/{id}', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ id: 'run-42' }))

    const result = await getRun('run-42')

    expect(result).toEqual({ id: 'run-42' })
    expect(fetch).toHaveBeenCalledWith('/api/runs/run-42', expect.anything())
  })

  it('getHarnessSummary GETs /api/harness/summary', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ id: 'batch-1' }))

    const result = await getHarnessSummary()

    expect(result).toEqual({ id: 'batch-1' })
    expect(fetch).toHaveBeenCalledWith('/api/harness/summary', expect.anything())
  })

  it('listLeads omits the query string when no status filter is given', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]))

    await listLeads()

    expect(fetch).toHaveBeenCalledWith('/api/leads', expect.anything())
  })

  it('listLeads appends ?status= when a filter is given', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]))

    await listLeads('accepted')

    expect(fetch).toHaveBeenCalledWith('/api/leads?status=accepted', expect.anything())
  })

  it('throws an ApiError with the parsed detail message on a JSON error body', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: 'Not implemented yet.' }, { status: 501 }),
    )

    await expect(createRun('hello')).rejects.toMatchObject({
      name: 'ApiError',
      status: 501,
      message: 'Not implemented yet.',
    })
  })

  it('throws an ApiError using the raw body text when the error body is not JSON', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('internal server error', { status: 500 }))

    const error = await createRun('hello').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).message).toBe('internal server error')
  })
})
