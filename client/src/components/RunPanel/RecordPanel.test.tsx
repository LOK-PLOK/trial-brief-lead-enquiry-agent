import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RecordPanel } from './RecordPanel'

describe('RecordPanel', () => {
  it('shows a "no run selected" placeholder when there is no record and status is not error', () => {
    render(<RecordPanel record={null} finalStatus="completed" />)
    expect(screen.getByText('No run selected.')).toBeInTheDocument()
  })

  it('explains a missing record differently when the run ended in error', () => {
    render(<RecordPanel record={null} finalStatus="error" />)
    expect(
      screen.getByText('No record was produced (execution stopped before completion).'),
    ).toBeInTheDocument()
  })

  it('renders extracted fields, score, and dedupe hash', () => {
    const record = {
      extracted: { name: 'Alex Tan', email: 'alex@example.com' },
      score: 87,
      dedupe_hash: 'abc123',
    }
    render(<RecordPanel record={record} finalStatus="completed" />)

    expect(screen.getByText('name')).toBeInTheDocument()
    expect(screen.getByText('Alex Tan')).toBeInTheDocument()
    expect(screen.getByText('email')).toBeInTheDocument()
    expect(screen.getByText('alex@example.com')).toBeInTheDocument()
    expect(screen.getByText('87')).toBeInTheDocument()
    expect(screen.getByTitle('abc123')).toBeInTheDocument()
  })

  it('always shows the final status badge, distinguishing quarantined from completed', () => {
    render(<RecordPanel record={{}} finalStatus="quarantined" />)
    expect(screen.getByText('quarantined')).toBeInTheDocument()
  })
})
