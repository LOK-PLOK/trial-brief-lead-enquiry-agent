import { ApiError } from './client'

/**
 * Turns an unknown query/mutation error into a human-readable message.
 * Surfaces the FastAPI structured error body (`ApiError.status` +
 * `.message`, see api/client.ts) so a 501 stub, a validation error, and a
 * genuine 500 are all distinguishable in the UI rather than collapsed into
 * a generic "something went wrong".
 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return `Request failed (${error.status}): ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong.'
}
