/**
 * Displays the 3 adversarial enquiries and what happened when run against
 * the finished system, including anything that succeeded (per the brief's
 * explicit instruction to report successes, not just catches).
 * See docs/architecture.md section 14 (adversarial sequence diagram).
 */
export function AdversarialReport() {
  // TODO(client/components/AdversarialReport): fetch runs tagged
  // `is_adversarial=true` (needs a dedicated API route or a filter param on
  // GET /api/runs — decide when implementing) and render each one's plan,
  // trace, record, and verifier outcome, with an explicit note on whether
  // the injection succeeded.
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-700">Adversarial Cases</h3>
      <p className="mt-2 text-sm text-slate-400">
        TODO: render the 3 adversarial runs and outcomes.
      </p>
    </div>
  )
}
