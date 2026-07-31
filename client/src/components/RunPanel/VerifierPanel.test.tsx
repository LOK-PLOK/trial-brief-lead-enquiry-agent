import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VerifierPanel } from './VerifierPanel'
import type { VerifierDecision } from '../../api/types'

const baseDecision: VerifierDecision = {
  pass: true,
  confidence: 0.95,
  fabrication_detected: false,
  fabricated_fields: [],
  plan_deviation_detected: false,
  deviation_details: null,
  reason: 'All fields verifiable against the enquiry text.',
}

describe('VerifierPanel', () => {
  it('shows a placeholder when there is no decision', () => {
    render(<VerifierPanel decision={null} />)
    expect(screen.getByText('No verifier decision for this run.')).toBeInTheDocument()
  })

  it('renders PASS with confidence and reason', () => {
    render(<VerifierPanel decision={baseDecision} />)
    expect(screen.getByText('PASS')).toBeInTheDocument()
    expect(screen.getByText('confidence 95%')).toBeInTheDocument()
    expect(screen.getByText(baseDecision.reason)).toBeInTheDocument()
  })

  it('renders FAIL with fabricated fields when fabrication is detected', () => {
    const decision: VerifierDecision = {
      ...baseDecision,
      pass: false,
      fabrication_detected: true,
      fabricated_fields: ['phone'],
      reason: 'Phone number does not appear anywhere in the enquiry text.',
    }
    render(<VerifierPanel decision={decision} />)

    expect(screen.getByText('FAIL')).toBeInTheDocument()
    expect(screen.getByText('detected', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText(/fields: phone/)).toBeInTheDocument()
  })

  it('renders plan deviation details when a deviation is detected', () => {
    const decision: VerifierDecision = {
      ...baseDecision,
      pass: false,
      plan_deviation_detected: true,
      deviation_details: 'Step 2 executed score_lead instead of lookup_jurisdiction_rule.',
      reason: 'Trace does not match the plan.',
    }
    render(<VerifierPanel decision={decision} />)

    expect(
      screen.getByText(/Step 2 executed score_lead instead of lookup_jurisdiction_rule\./),
    ).toBeInTheDocument()
  })
})
