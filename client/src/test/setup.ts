/**
 * Vitest setup file (see vite.config.ts `test.setupFiles`). Registers the
 * jest-dom matchers (`toBeInTheDocument`, etc.) used across component tests,
 * and unmounts rendered components after every test -- without this,
 * multiple tests in the same file that each call `render()` would leave
 * stale DOM behind and cause false "found multiple elements" failures.
 */
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

afterEach(() => {
  cleanup()
})
