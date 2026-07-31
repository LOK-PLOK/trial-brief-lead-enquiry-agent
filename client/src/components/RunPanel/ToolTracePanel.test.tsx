import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ToolTracePanel } from './ToolTracePanel'
import type { Plan, ToolCallTrace } from '../../api/types'

describe('ToolTracePanel', () => {
  it('shows a placeholder when no trace is available', () => {
    render(<ToolTracePanel trace={null} />)
    expect(screen.getByText('No run selected.')).toBeInTheDocument()
  })

  it('renders each call with its status and latency', () => {
    const trace: ToolCallTrace = {
      calls: [
        {
          step: 1,
          tool: 'parse_enquiry',
          args: {},
          status: 'success',
          result: { name: 'Alex' },
          error: null,
          latency_ms: 123,
        },
        {
          step: 2,
          tool: 'write_record',
          args: {},
          status: 'error',
          result: null,
          error: 'duplicate',
          latency_ms: 45,
        },
      ],
    }
    render(<ToolTracePanel trace={trace} />)

    expect(screen.getByText('1. parse_enquiry')).toBeInTheDocument()
    expect(screen.getByText('success · 123ms')).toBeInTheDocument()
    expect(screen.getByText('2. write_record')).toBeInTheDocument()
    expect(screen.getByText('error · 45ms')).toBeInTheDocument()
    expect(screen.getByText('Error: duplicate')).toBeInTheDocument()
  })

  it('flags a plan deviation when the executed tool differs from the planned tool', () => {
    const plan: Plan = {
      steps: [{ step: 1, tool: 'lookup_jurisdiction_rule', args: {}, rationale: 'check rules' }],
    }
    const trace: ToolCallTrace = {
      calls: [
        {
          step: 1,
          tool: 'score_lead',
          args: {},
          status: 'success',
          result: {},
          error: null,
          latency_ms: 10,
        },
      ],
    }
    render(<ToolTracePanel trace={trace} plan={plan} />)

    expect(screen.getByText(/Plan deviation/)).toBeInTheDocument()
  })

  it('does not flag a deviation when the executed tool matches the plan', () => {
    const plan: Plan = {
      steps: [{ step: 1, tool: 'score_lead', args: {}, rationale: 'score it' }],
    }
    const trace: ToolCallTrace = {
      calls: [
        {
          step: 1,
          tool: 'score_lead',
          args: {},
          status: 'success',
          result: {},
          error: null,
          latency_ms: 10,
        },
      ],
    }
    render(<ToolTracePanel trace={trace} plan={plan} />)

    expect(screen.queryByText(/Plan deviation/)).not.toBeInTheDocument()
  })
})
