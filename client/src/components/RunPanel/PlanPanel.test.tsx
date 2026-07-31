import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PlanPanel } from './PlanPanel'
import type { Plan } from '../../api/types'

describe('PlanPanel', () => {
  it('shows a placeholder when no plan is available', () => {
    render(<PlanPanel plan={null} />)
    expect(screen.getByText('No run selected.')).toBeInTheDocument()
  })

  it('renders each plan step in order with its tool and rationale', () => {
    const plan: Plan = {
      steps: [
        { step: 1, tool: 'parse_enquiry', args: { text: 'hi' }, rationale: 'extract fields' },
        {
          step: 2,
          tool: 'lookup_jurisdiction_rule',
          args: { country: 'SG' },
          rationale: 'check rules',
        },
      ],
    }
    render(<PlanPanel plan={plan} />)

    expect(screen.getByText('1. parse_enquiry')).toBeInTheDocument()
    expect(screen.getByText('extract fields')).toBeInTheDocument()
    expect(screen.getByText('2. lookup_jurisdiction_rule')).toBeInTheDocument()
    expect(screen.getByText('check rules')).toBeInTheDocument()
  })

  it('handles a plan with zero steps distinctly from a null plan', () => {
    render(<PlanPanel plan={{ steps: [] }} />)
    expect(screen.getByText('Plan has no steps.')).toBeInTheDocument()
  })
})
