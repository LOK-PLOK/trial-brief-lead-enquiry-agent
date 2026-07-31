import type { ToolCallTrace } from '../../api/types'

interface DuplicateCheckBannerProps {
  trace: ToolCallTrace | null
}

/**
 * Surfaces `write_record`'s duplicate rejection as its own, distinctly
 * labeled callout, separate from the generic Tool Trace / error display
 * (requirement: "Display duplicate-test result separately"). Duplicate
 * detection is exercised by a dedicated integration test
 * (server/app/tests/integration/test_pipeline_duplicate_detection.py) kept
 * outside the 45-run harness -- submitting the same enquiry twice through
 * the Run tab is how a human can trigger and observe the same behaviour
 * live, which is exactly the manual-testing counterpart to that test.
 */
export function DuplicateCheckBanner({ trace }: DuplicateCheckBannerProps) {
  const duplicateCall = trace?.calls.find(
    (call) => call.tool === 'write_record' && call.status === 'error' && call.error === 'duplicate',
  )
  if (!duplicateCall) {
    return null
  }

  return (
    <div
      role="status"
      className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800 md:col-span-2"
    >
      <p className="font-semibold">Duplicate detected</p>
      <p className="mt-1 text-xs">
        <code>write_record</code> rejected this submission: its normalized email/phone hash matches
        an already-accepted lead. This is a controlled tool failure, distinct from an unexpected
        system error, and is verified by a dedicated test kept separate from the 45-run harness (see
        the Harness Summary tab).
      </p>
    </div>
  )
}
