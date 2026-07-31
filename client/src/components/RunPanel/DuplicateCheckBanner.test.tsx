import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DuplicateCheckBanner } from './DuplicateCheckBanner'
import type { ToolCallTrace } from '../../api/types'

describe('DuplicateCheckBanner', () => {
  it('renders nothing when there is no trace', () => {
    const { container } = render(<DuplicateCheckBanner trace={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when write_record succeeded', () => {
    const trace: ToolCallTrace = {
      calls: [
        {
          step: 1,
          tool: 'write_record',
          args: {},
          status: 'success',
          result: {},
          error: null,
          latency_ms: 5,
        },
      ],
    }
    const { container } = render(<DuplicateCheckBanner trace={trace} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when write_record failed for a reason other than duplicate', () => {
    const trace: ToolCallTrace = {
      calls: [
        {
          step: 1,
          tool: 'write_record',
          args: {},
          status: 'error',
          result: null,
          error: 'validation_error',
          latency_ms: 5,
        },
      ],
    }
    const { container } = render(<DuplicateCheckBanner trace={trace} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders a distinct duplicate-detected callout when write_record rejects a duplicate', () => {
    const trace: ToolCallTrace = {
      calls: [
        {
          step: 1,
          tool: 'write_record',
          args: {},
          status: 'error',
          result: null,
          error: 'duplicate',
          latency_ms: 5,
        },
      ],
    }
    render(<DuplicateCheckBanner trace={trace} />)

    expect(screen.getByText('Duplicate detected')).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
})
