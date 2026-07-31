import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'

/** A fresh, retry-disabled QueryClient per test so error states surface immediately. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

export function renderWithQueryClient(
  ui: ReactElement,
  client: QueryClient = createTestQueryClient(),
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return { client, ...render(ui, { wrapper: Wrapper }) }
}
