import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RunPanel } from './index'
import type { RunResult } from '../../api/types'

function buildRun(overrides: Partial<RunResult> = {}): RunResult {
  return {
    id: 'run-1',
    enquiry_text: 'Hi, interested in whisky casks.',
    enquiry_id: null,
    repeat_index: null,
    is_adversarial: false,
    plan: { steps: [{ step: 1, tool: 'parse_enquiry', args: {}, rationale: 'extract fields' }] },
    plan_schema_valid: true,
    tool_call_trace: {
      calls: [
        {
          step: 1,
          tool: 'parse_enquiry',
          args: {},
          status: 'success',
          result: { name: 'Alex' },
          error: null,
          latency_ms: 120,
        },
      ],
    },
    final_record: { extracted: { name: 'Alex' }, score: 80 },
    verifier_decision: {
      pass: true,
      confidence: 0.9,
      fabrication_detected: false,
      fabricated_fields: [],
      plan_deviation_detected: false,
      deviation_details: null,
      reason: 'Looks good.',
    },
    repair_attempted: false,
    repair_succeeded: null,
    final_status: 'completed',
    error_type: null,
    error_message: null,
    traceback: null,
    llm_calls: [],
    total_tokens: 100,
    total_cost_usd: 0.001,
    total_latency_ms: 500,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('RunPanel', () => {
  it('shows all "no run selected" placeholders when run is null', () => {
    render(<RunPanel run={null} />)

    expect(screen.getAllByText(/No run selected\.|no run selected/i).length).toBeGreaterThan(0)
  })

  it('renders plan, trace, record, verifier decision, and cost for a completed run', () => {
    render(<RunPanel run={buildRun()} />)

    expect(screen.getByText('Plan')).toBeInTheDocument()
    expect(screen.getByText('Tool Call Trace')).toBeInTheDocument()
    expect(screen.getByText('Final Record')).toBeInTheDocument()
    expect(screen.getByText('Verifier Decision')).toBeInTheDocument()
    expect(screen.getByText('PASS')).toBeInTheDocument()
    expect(screen.getByText(/100 tokens/)).toBeInTheDocument()
  })

  it('surfaces the duplicate-detected banner when write_record rejects a duplicate', () => {
    const run = buildRun({
      final_status: 'error',
      final_record: null,
      tool_call_trace: {
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
      },
    })
    render(<RunPanel run={run} />)

    expect(screen.getByText('Duplicate detected')).toBeInTheDocument()
  })
})
