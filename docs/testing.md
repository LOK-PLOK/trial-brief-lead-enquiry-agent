# Testing Guide

How to verify the Lead Enquiry Agent: automated tests, evaluation harness, browser UI, API/Postman, and adversarial probes.

**Audience:** Assessors and operators preparing a submission demo.  
**Data policy:** Use fictional contacts only.  
**LLM note:** Judge contracts and structure (tool order, statuses, field presence, DB side-effects), not exact model prose.

| Field | Value |
| --- | --- |
| Backend | `http://localhost:8000` |
| Swagger | `http://localhost:8000/docs` |
| Frontend (dev) | `http://localhost:5173` |
| Health | `GET /api/health` → `{"status":"ok"}` |

Related docs: [`architecture.md`](architecture.md), [`contracts.md`](contracts.md), [`deployment.md`](deployment.md).

---

## Pipeline under test

```
Planner
  → Executor (parse_enquiry → lookup_jurisdiction_rule → score_lead)
  → Verifier
  → optional Repair   (never calls write_record)
  → write_record      (ONLY AFTER verifier passes)
  → persist RunResult
```

| Stage | Writes a `leads` row? |
| --- | --- |
| Executor / Verifier / Repair | No |
| `write_record` after Verifier pass | Yes (one row per accepted enquiry) |
| Quarantine | No |
| Duplicate after verify | No second lead (`write_record` error `duplicate`) |

Happy-path tool trace: `parse_enquiry` → `lookup_jurisdiction_rule` → `score_lead` → `write_record` (last, post-verify).  
`plan.steps` must not require `write_record`; the Orchestrator appends it after the gate.

---

## 1. Unit testing

Backend unit tests live under `server/app/tests/unit/` (planner, executor, verifier, repair, tools, OpenRouter adapter, harness job/dashboard helpers, etc.).

```bash
cd server
source .venv/bin/activate
python -m pytest app/tests/unit -q
```

Frontend unit tests (Vitest) live under `client/src/`:

```bash
cd client
npm test
```

Evaluation package unit tests (harness orchestration, metrics, datasets, parser, adversarial runner):

```bash
# from repo root
PYTHONPATH=server python -m pytest evaluation/tests -q
```

---

## 2. Integration testing

Backend integration tests under `server/app/tests/integration/` cover the API, repository/DB, orchestrator/executor/verifier with fakes, and duplicate detection through the real persistence path.

```bash
cd server
source .venv/bin/activate
python -m pytest app/tests/integration -q
```

Typical suite snapshot (from project verification notes): backend pytest, evaluation pytest, and client vitest all pass locally when dependencies and a throwaway test DB are available. Re-run before submission.

---

## 3. Harness testing

The evaluation harness calls the **same** `Pipeline.run()` as the live API (`docs/architecture.md` section 11). Default shape: 15 enquiries × 3 repeats = 45 runs (use `1×` for smoke tests).

### From the UI (recommended for assessors)

1. Open **Harness Testing**.
2. Choose a dataset: **Official Trial A Dataset (E01–E15)**, **Manual Test Suite (E01–E14)**, **Adversarial**, **Custom upload / paste**, or **Single Enquiry**.
3. Select **1×** or **3×** repeats (custom/single also support paste/upload of `.txt` / `.md` / `.csv` / `.json`).
4. Click **Run harness** (or **Run enquiry** in single mode).
5. Watch progress; open **Summary** for rates, cost, latency, pipeline health, charts, and the per-run table.
6. Click a row for Overview / Timeline / Planner / Tool Trace / Verifier / Repair / Raw JSON.

Job APIs: `GET /api/harness/datasets`, `POST /api/harness/jobs`, `GET /api/harness/jobs/{id}`, `POST /api/harness/jobs/{id}/cancel`, `GET /api/harness/batches/{id}/dashboard`, `POST /api/harness/parse`.

### From the CLI

```bash
# from repo root — requires OPENROUTER_API_KEY for live LLM calls
PYTHONPATH=server python -m evaluation.harness
PYTHONPATH=server python -m evaluation.adversarial
```

Reports write under `evaluation/reports/`. Metrics also appear via `GET /api/harness/summary` after a batch finishes. Some rates are intentionally `null` where ground truth is unavailable (see honesty notes in the metrics payload and [`failure_log.md`](failure_log.md)).

**Cost warning:** a full 45-run live harness is many LLM calls. Prefer **1× Standard** for demos if credits are limited.

---

## 4. Browser testing

Prerequisites: backend on `:8000`, frontend on `:5173` (or Docker serving both on `:8000`). Optional clean DB:

```bash
# stop uvicorn first
rm -f server/data/app.db server/data/app.db-wal server/data/app.db-shm
# restart uvicorn — schema recreates on boot
```

### Run tab

Paste an enquiry → **Run**. Confirm panels: Plan, Tool Trace, Final Record, Verifier, cost/latency.

| Scenario | What to look for |
| --- | --- |
| Valid high-value enquiry | Prefer `completed`; score high band; `write_record` last |
| Missing / thin fields | Structured result; often quarantine or sparse record; no invented contacts |
| Prompt injection (Adversarial tab → load sample) | Mandatory tools not skipped; no premature write |
| Duplicate (same email+phone as a prior completed run) | `write_record` duplicate; one lead remains |
| Repair / quarantine path | `repair_attempted` / `repair_succeeded` or `quarantined` with reason |

### History tab

Newest runs first; click a row for detail; accepted leads listed when present. (No pagination controls in the UI; API lists a recent page.)

### Harness Testing tab

See §3. Verify progress, cancel, summary charts, and run detail tabs after a short job.

### Adversarial tab

Load a sample into the Run tab and submit; inspect plan deviation / fabrication outcomes.

---

## 5. Postman / API testing

No auth. Collection variable `baseUrl` = `http://localhost:8000`.

| Method | URL | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness |
| `POST` | `/api/runs` | Body `{"enquiry_text":"..."}` → full `RunResult` |
| `GET` | `/api/runs` | Summaries, newest first |
| `GET` | `/api/runs/{id}` | Full detail (`404` if unknown) |
| `GET` | `/api/leads` | Accepted leads |
| `GET` | `/api/harness/datasets` | Named datasets |
| `POST` | `/api/harness/parse` | Preview-parse paste/upload |
| `POST` | `/api/harness/jobs` | Start async harness (`n_repeats` ∈ {1, 3}) |
| `GET` | `/api/harness/jobs/{id}` | Poll progress |
| `POST` | `/api/harness/jobs/{id}/cancel` | Cooperative cancel |
| `GET` | `/api/harness/batches/{id}/dashboard` | Stats, charts, run rows |
| `GET` | `/api/harness/summary` | Latest batch summary |
| `GET` | `/api/harness/runs` | Runs for latest batch |
| `GET` | `/api/harness/runs/{id}/timeline` | Stage timeline |

Swagger equivalent: open `/docs` → **Try it out**.

**Note:** Pipeline business failures usually return HTTP `200` with `final_status` of `completed`, `quarantined`, or `error`. Malformed JSON → `422`.

### Example happy-path body

```json
{
  "enquiry_text": "Hello,\n\nMy name is Olivia Hart. Email olivia.hart@example.com, phone +61 412 555 018. I am based in Australia.\n\nI want to buy a premium Scotch whisky cask portfolio around AUD 120,000 this month — please call me urgently.\n\nRegards,\nOlivia"
}
```

Expect roughly: `budget_band=C` (AUD 120,000 ≈ USD 81,000–84,000, official band C is USD 50,000–249,999), `asset_interest=Whisky cask`, `urgency=Immediate` ("urgently"), score ~75 (30 budget + 30 urgency + 15 jurisdiction), `final_status=completed`, lead inserted.

### Example low-budget body

```json
{
  "enquiry_text": "Hello, I'm Priya Nair in the United Kingdom. priya.nair@example.co.uk, +44 7700 900123.\n\nJust browsing — maybe a small cask under £5,000 someday. No rush at all.\n\nCheers"
}
```

Expect roughly: `budget_band=A` (under £5,000 ≈ under USD 6,500, official band A is under USD 10,000), `asset_interest=Whisky cask`, `urgency=Exploratory` ("no rush at all"), score ~30 (10 budget + 5 urgency + 15 jurisdiction), prefer `completed`.

### Example restricted jurisdiction (US)

Use a US-based enquiry with clear contacts and budget. Expect `jurisdiction_rule.restricted=true` and reduced jurisdiction score component.

### Duplicate check

Submit the Olivia Hart body twice. Second run: tools may succeed; `write_record` returns `duplicate`; still one lead.

---

## 6. Adversarial testing

Fixtures: `evaluation/fixtures/adversarial_enquiries.json` (three cases, including prompt-injection style text).

```bash
PYTHONPATH=server python -m evaluation.adversarial
```

Or: **Adversarial** tab → **Load into Run tab** → submit. Confirm the Planner still plans mandatory tools and the Verifier/plan-deviation path records an honest outcome (injection must not become trusted system instructions).

---

## 7. Representative manual scenarios

The primary dataset is the 15 official Trial A enquiries (`evaluation/fixtures/enquiries.json`,
wording verbatim from `docs/WCC_Trial_A_Enquiry_Samples.md`). A representative subset, with the
official enum values each is expected to land on:

| ID | Enquirer / country | Intent | Expectation (approx.) |
| --- | --- | --- | --- |
| E01 | Daniel Okafor, UK | GBP 40,000 Speyside cask, "within the next few weeks" | `budget_band=C` (~USD 50–52k), `urgency=Within three months`; score ~60; completed |
| E03 | Hiroshi Tanaka, Japan | USD 300,000, "in no particular hurry" | `budget_band=D`, `urgency=Exploratory`; score ~60; completed |
| E04 | Faizal, Malaysia | "how much? i am from malaysia", no email | Thin/garbled; `budget_band=Unknown`, `urgency=Unknown`; no fabricated email preferred; often weak completed |
| E06 | Sam Reyes | "Interested. Tequila barrels." — one line, no budget/urgency | `asset_interest=Tequila barrel`; `budget_band=Unknown`, `urgency=Unknown`; completed with a sparse record |
| E10 | Yusuf Al-Rashid, UAE | USD 150,000, "ready to proceed quickly, ideally this month" | `budget_band=C`, `urgency=Immediate`; score ~75; completed |
| E12 | J.W. (no country given) | "low six figures", diversifying from wine into tequila | No country → exercises `lookup_jurisdiction_rule`'s missing/unknown-country handling; `asset_interest=Multiple` plausible (wine + tequila) |
| E14 | Elena Volkova, Cyprus | EUR 250,000, "want to move fast... within the fortnight" | `budget_band=D` (~USD 262–275k), `urgency=Immediate`; score ~85 (near-max); completed |

None of the 15 official enquiries is US- or China-based, so the `restricted=true` jurisdiction
path (0 jurisdiction points, e.g. `United States` in `server/app/data/jurisdiction_rules.json`) is
not exercised by the standard dataset — test it manually with a US-based enquiry body, or via the
adversarial fixtures.

Full scoring bands (deterministic once extracted — `server/app/tools/score_lead.py`): budget
`D`=40 / `C`=30 / `B`=20 / `A`=10 / `Unknown`=0; urgency `Immediate`=30 / `Within three months`=15
/ `Exploratory`=5 / `Unknown`=0; jurisdiction 15 (disclaimer required, not restricted — the common
case for every rule in `jurisdiction_rules.json` including the default) / 20 (no disclaimer
required — not currently reachable by any configured rule) / 0 (restricted). Max **90**.

---

## 8. Brief-aligned checklist

### Hard gates

- [ ] Public URL works from a cold network (see [`proof_note.md`](proof_note.md))
- [ ] Verifier catches a deliberate fabrication case
- [ ] Harness numbers reproduce on re-run

### Agent loop

- [ ] Planner returns a schema-valid `Plan` and does not execute tools
- [ ] Executor trace: parse → lookup → score
- [ ] `write_record` only after verifier pass
- [ ] Repair never inserts a lead before re-verify
- [ ] Quarantine is visible (status + reason), never a silent drop

### Display

- [ ] UI shows plan, tool trace, final record, verifier decision/reason, cost
- [ ] Harness Testing summary and charts load after a batch

### Deliverables

- [ ] Fictional data only
- [ ] Proof note ≤ 400 words
- [ ] Failure log specific and non-empty ([`failure_log.md`](failure_log.md))

---

## 9. Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Every new lead is duplicate | Stale DB / same contacts | Reset SQLite files (§4) |
| Quarantine but a lead exists | Old pre-refactor rows | Reset DB |
| `500` on `POST /api/runs` | Missing/invalid API key | Check `.env` / OpenRouter credits |
| `422` on `POST` | Bad JSON | Fix body |
| Empty harness summary | No finished batch | Run harness (UI or CLI) |
| Runs show `error` with provider text | OpenRouter credit/rate limit | Top up credits; reduced `max_tokens` helps reservation pressure |
| Score ≠ expectation | Extraction band differs | Check extracted fields; scoring itself is deterministic |

---

## 10. Short demo path (~5 minutes)

1. Start backend and frontend (or Docker).  
2. Run tab → submit Olivia Hart enquiry → show Plan, Tool Trace, Final Record.  
3. History → newest run.  
4. Harness Testing → Official Trial A Dataset (E01–E15) **1×** → watch progress → Summary → open one run detail.  
5. Optional: Custom paste with `E01` / `E02` blocks to show parser preview.  
6. Optional: Adversarial load → Run.
