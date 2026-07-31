import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryState } from './QueryState'
import { ApiError } from '../../api/client'

describe('QueryState', () => {
  it('renders the loading label while isLoading is true', () => {
    render(
      <QueryState isLoading error={null} loadingLabel="Loading things…">
        <p>content</p>
      </QueryState>,
    )
    expect(screen.getByText('Loading things…')).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('renders a formatted ApiError message with status code', () => {
    render(
      <QueryState isLoading={false} error={new ApiError(501, 'Not implemented yet.')}>
        <p>content</p>
      </QueryState>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Request failed (501): Not implemented yet.',
    )
  })

  it('renders a generic Error message when the error is a plain Error', () => {
    render(
      <QueryState isLoading={false} error={new Error('network down')}>
        <p>content</p>
      </QueryState>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('network down')
  })

  it('renders children once loading has finished and there is no error', () => {
    render(
      <QueryState isLoading={false} error={null}>
        <p>content</p>
      </QueryState>,
    )
    expect(screen.getByText('content')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
