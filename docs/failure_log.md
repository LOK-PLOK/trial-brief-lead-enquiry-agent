# Failure Log

**Candidate:** Paul France M. Detablan  
**Brief:** WCC-TRIAL-A  

An empty failure log is a failure to test. Gaps below are named on purpose.

## What broke or was hard

1. **Scaffold vs runnable gap.** Early phases left tools, API routes, and the LLM adapter as `NotImplementedError` / HTTP 501 stubs by design. Closing that gap required OpenRouter integration, four tool implementations, and wiring `Pipeline.run()` into FastAPI without redesigning contracts.

2. **Blank `DATABASE_URL=`.** Shipping `.env.example` with an empty `DATABASE_URL` made pydantic-settings override the SQLite default with `""`, breaking DB boot and many tests until a blank→default validator was added.

3. **`POST /api/runs` dependency crash.** Before the adapter existed, `Depends(get_adapter)` raised during dependency resolution and returned HTTP 500 instead of a clean 501 — fixed by implementing OpenRouter and a real route body.

4. **Repair Loop deferred then required.** The Orchestrator originally quarantined on verifier fail without repair. Brief §3.5 and contracts require one repair pass — now implemented (`reextract` / `reexecute` / `replan`) with a fresh Verifier call afterward.

5. **Post-verify persistence redesign.** Original architecture ran `write_record` inside the Executor before Verifier, so Repair's full-plan re-run self-collided on `dedupe_hash` and falsely quarantined new leads. Corrected: Verifier is the gate; Orchestrator runs `write_record` once after pass; Repair never persists; quarantine inserts no lead.

6. **Verifier treated legitimate tool-derived values as fabricated.** Fabrication was defined too narrowly (enquiry text only), so `handling_note`, `score`, and `jurisdiction_rule` looked “invented.” Resolution: verify extracted fields against the enquiry; verify derived fields against successful tool outputs.

7. **Deterministic match did not clear LLM false positives.** After the split above, derived-field checks found no mismatches, but the Verifier still returned `fabrication_detected=true` for tool-backed fields. Resolution: after the LLM call, clear fabrication claims on tool-derived fields that match successful tool outputs and on `dedupe_hash` when recomputation matches.

8. **Official 15 enquiry samples not in-repo.** Brief §3.1 says samples would be supplied. Stand-in fictional fixtures were added so the harness is runnable; replace with the official set when received and re-run for the reproducibility gate.

9. **Extractor returned `unknown` for clear budget/urgency evidence.** Manual runs quarantined when amounts and phrases were present but bands stayed `unknown`. Resolution: expanded `parse_enquiry` system prompt with budget/urgency mapping examples — still a single structured LLM call (no regex parsers).

10. **Verifier treated planner placeholder args as evidence.** Plan step `args` placeholders were mistaken for tool evidence. Resolution: redact plan step `args` in the Verifier user prompt; add a deterministic check that `final_record.extracted` matches successful `parse_enquiry` output.

11. **Verifier rejected valid `urgency=low` for “Not urgent”.** The model claimed a level below `low` — impossible under the closed enum. Resolution: Verifier prompt treats enums as closed and accepts supported values (e.g. “not urgent” → `low`).

12. **City mistaken for country (open).** E10 (“calling from Lagos”) can extract `country=Lagos`; jurisdiction lookup falls back to `default`. Prefer a country when only a city is stated — not yet hardened in the extractor prompt.

13. **Verifier acted as a second extractor on urgency.** Extractor set `urgency=low` for clear “not urgent” language, but the Verifier re-chose another band. Resolution: extracted-field check redefined as SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE — ask whether a reasonable extractor could produce the value; never fail for preference among valid enums.

14. **Extractor under-banded £95k as medium.** Guidance already said ≥70k → high, but the model still returned `medium` for “approximately £95,000”. Resolution: strengthened budget rules (currency symbols equivalent; approximators do not change the band; explicit high-band examples).

15. **Verifier LLM produced self-contradictory decisions.** The model sometimes affirmed a value in `reason` while listing it in `fabricated_fields`. Prompt-only fixes were insufficient. Proper architecture fix is item 16.

16. **Verifier architecture: unconstrained fabrication list.** Root cause: free-text `fabricated_fields` let the model decide quarantine in one unconstrained step. Re-running `parse_enquiry` inside the Verifier was evaluated and rejected. Resolution: structured `extracted_field_verdicts`; deterministic closed-enum support classifier (`agent/extracted_support.py`); contradiction gate that admits **only CONTRADICTED** extracted fields into `fabricated_fields`. Planner / Executor / scoring unchanged.

## What I could not finish

1. **True fabrication-caught rate.** Needs ground-truth labels independent of the Verifier. Reported as `None` with an honesty note.
2. **First-attempt schema-breach rates.** Adapter retries are internal to `complete_structured()`; surfacing them would extend an immutable contract. Reported as `None`.
3. **Live adversarial / 45-run numbers on production.** Code and fixtures are ready; full live runs need a funded OpenRouter key and a public Render URL.
4. **Proof-note URL field.** Filled only after the cold-network check post-deploy.

## What I would do with another week

1. Seed a small labelled fabrication set so `fabrication_caught_rate` is measurable.
2. Optionally extend `StructuredCompletionResponse` (with a contracts revision) to expose first-attempt schema failures.
3. Add a Render Disk for durable SQLite across free-tier restarts.
4. Run adversarial + full harness on the live URL and attach reports under `evaluation/reports/`.
5. Tighten jurisdiction rules against the official enquiry set once supplied.
