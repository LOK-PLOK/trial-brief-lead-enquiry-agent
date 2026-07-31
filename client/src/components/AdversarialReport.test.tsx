import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AdversarialReport } from './AdversarialReport'

describe('AdversarialReport', () => {
  it('renders the sample adversarial enquiries with a load action each', () => {
    render(<AdversarialReport onLoadEnquiry={vi.fn()} />)

    const loadButtons = screen.getAllByRole('button', { name: 'Load into Run tab' })
    expect(loadButtons.length).toBeGreaterThanOrEqual(3)
  })

  it('calls onLoadEnquiry with the sample text when a load button is clicked', async () => {
    const onLoadEnquiry = vi.fn()
    const user = userEvent.setup()
    render(<AdversarialReport onLoadEnquiry={onLoadEnquiry} />)

    const [firstButton] = screen.getAllByRole('button', { name: 'Load into Run tab' })
    await user.click(firstButton)

    expect(onLoadEnquiry).toHaveBeenCalledTimes(1)
    expect(onLoadEnquiry.mock.calls[0][0]).toEqual(expect.any(String))
    expect((onLoadEnquiry.mock.calls[0][0] as string).length).toBeGreaterThan(0)
  })
})
