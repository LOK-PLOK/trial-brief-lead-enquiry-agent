# Evaluation Harness Report

**Harness batch:** `b6e973e4-e437-4644-a11b-00f667bbb3ca`  
**Generated:** 2026-08-03T12:38:21.001966+00:00  
**Dataset:** Official Trial A Dataset (E01–E15)  
**Runs:** 45 executed, 0 skipped (resumed), 45 expected (15 enquiries x 3 repeats)

## Summary

| Metric | Value |
|---|---|
| Completion rate | 0.222 |
| Quarantined rate | 0.222 |
| Error rate | 0.556 |
| Verifier pass rate | 0.722 |
| Fabrication rate (flagged by verifier) | 0.278 |
| Fabrication caught rate | n/a |
| Planner schema breach rate | n/a |
| Extractor schema breach rate | n/a |
| Tool selection accuracy | 0.422 |
| Repair attempted rate | 0.333 |
| Repair success rate | 0.333 |

## Latency / Tokens / Cost

| Metric | Value |
|---|---|
| Mean latency (ms) | 12329.530 |
| Median latency (ms) | 12130.764 |
| p95 latency (ms) | 18509.860 |
| Mean tokens | 16059.444 |
| Mean cost (USD) | 0.002664 |

## Run-to-run variance (grouped by enquiry)

Overall latency stdev (ms): 3996.093  
Overall cost stdev (USD): 0.001006

| Enquiry | Repeats | Final statuses | Latency stdev (ms) | Cost stdev (USD) | Distinct final_record shapes |
|---|---|---|---|---|---|
| E01 | 3 | completed, error | 1421.277 | 0.000024 | 1 |
| E02 | 3 | quarantined | 2005.267 | 0.000012 | 2 |
| E03 | 3 | completed, error | 319.901 | 0.000023 | 1 |
| E04 | 3 | completed, error | 1149.176 | 0.000017 | 1 |
| E05 | 3 | completed, error | 1047.494 | 0.000001 | 1 |
| E06 | 3 | error | 1562.592 | 0.000000 | 1 |
| E07 | 3 | completed, error | 759.113 | 0.000027 | 1 |
| E08 | 3 | completed, quarantined | 2977.296 | 0.000778 | 2 |
| E09 | 3 | quarantined | 1913.937 | 0.000026 | 2 |
| E10 | 3 | error | 732.392 | 0.000003 | 1 |
| E11 | 3 | completed, error | 420.511 | 0.000035 | 1 |
| E12 | 3 | error | 714.136 | 0.000005 | 1 |
| E13 | 3 | completed, error, quarantined | 2594.168 | 0.000775 | 3 |
| E14 | 3 | completed, error, quarantined | 3215.426 | 0.000774 | 1 |
| E15 | 3 | completed, error | 1957.813 | 0.000794 | 2 |

## Honesty notes

- fabrication_caught_rate is None: it requires a ground-truth fabrication label independent of the Verifier's own opinion (e.g. seeded unanswerable fields), which this harness does not have -- see docs/architecture.md section 11.
- planner_schema_breach_rate / extractor_schema_breach_rate are None: ModelAdapter.complete_structured() retries a schema-invalid first attempt internally and opaquely per its own contract (docs/contracts.md section 5); no field on StructuredCompletionResponse currently surfaces whether that happened. Extending that contract would be a deliberate contracts.md change -- named here rather than guessed at, per docs/architecture.md section 15's honesty policy.
- repair_success_rate is None whenever repair_attempted_rate is 0.0 (no verifier failures triggered the Repair Loop in this batch).
