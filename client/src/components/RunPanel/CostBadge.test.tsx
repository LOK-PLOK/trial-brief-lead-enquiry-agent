import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CostBadge } from './CostBadge'
import type { LlmCallUsage } from '../../api/types'

describe('CostBadge', () => {
  it('renders total tokens, cost, and latency', () => {
    render(<CostBadge totalTokens={1234} totalCostUsd={0.001234} totalLatencyMs={2500} />)

    expect(screen.getByText(/1,234 tokens/)).toBeInTheDocument()
    expect(screen.getByText(/\$0\.001234/)).toBeInTheDocument()
    expect(screen.getByText(/2\.50s/)).toBeInTheDocument()
  })

  it('renders a per-stage breakdown when llmCalls are provided', () => {
    const calls: LlmCallUsage[] = [
      {
        stage: 'planner',
        model_provider: 'openai',
        model_name: 'gpt-4o-mini',
        prompt_tokens: 100,
        completion_tokens: 50,
        cost_usd: 0.0005,
        latency_ms: 800,
      },
      {
        stage: 'verifier',
        model_provider: 'openai',
        model_name: 'gpt-4o-mini',
        prompt_tokens: 200,
        completion_tokens: 20,
        cost_usd: 0.0007,
        latency_ms: 600,
      },
    ]
    render(
      <CostBadge totalTokens={370} totalCostUsd={0.0012} totalLatencyMs={1400} llmCalls={calls} />,
    )

    expect(screen.getByText('planner')).toBeInTheDocument()
    expect(screen.getByText('verifier')).toBeInTheDocument()
    expect(screen.getByText('150')).toBeInTheDocument()
    expect(screen.getByText('220')).toBeInTheDocument()
  })

  it('omits the breakdown table when no llmCalls are given', () => {
    render(<CostBadge totalTokens={0} totalCostUsd={0} totalLatencyMs={0} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
