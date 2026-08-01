# Lead Enquiry Agent

A two-role agent system (Planner + Executor) with an independent LLM verifier gate and a
reproducible evaluation harness, built for the Whisky Cask Club technical trial.

Given a single block of unstructured lead-enquiry text, the system produces a verified,
structured lead record: an LLM Planner decides which tools to call and in what order, a
deterministic Executor carries out that plan via real tool/function calls, and a fully
independent LLM Verifier checks the result for fabrication and plan deviation before it is
accepted. On failure, a failed run is quarantined rather than silently dropped (the repair
pass described in `docs/architecture.md` section 10 is not implemented — see
`docs/implementation_status.md` for the full, current, honest status of every piece).

The system is fully functional end-to-end today, using [OpenRouter](https://openrouter.ai) as
the LLM provider. See [`docs/deployment.md`](docs/deployment.md) for environment variables,
local setup, and how to deploy it to Render.

## Documentation

- [`docs/Trial_Brief_Paul_Detablan.md`](docs/Trial_Brief_Paul_Detablan.md) — the original brief.
- [`docs/architecture.md`](docs/architecture.md) — source of truth for system design.
- [`docs/development-rules.md`](docs/development-rules.md) — source of truth for implementation rules.
- [`docs/contracts.md`](docs/contracts.md) — source of truth for every immutable input/output contract.
- [`docs/deployment.md`](docs/deployment.md) — environment variables, local/production setup, Render deployment, troubleshooting.
- [`docs/manual_testing.md`](docs/manual_testing.md) — step-by-step manual testing walkthrough (Swagger JSON, UI clicks, DB, Docker, submission checklist).
- [`docs/implementation_status.md`](docs/implementation_status.md) — current, evidence-based status of every piece.
- [`docs/proof_note.md`](docs/proof_note.md) — trial deliverable: what was built, measured, and how the live URL was checked.
- [`docs/failure_log.md`](docs/failure_log.md) — trial deliverable: what broke, what was unfinished, what another week would buy.

## Repository layout

```
server/       FastAPI backend: agent pipeline, tools, LLM adapters, DB, API
client/       React (Vite + TypeScript) single-page frontend
evaluation/   Standalone harness that drives the 45-run evaluation + adversarial cases
docs/         Brief, architecture, and development-rules documentation
```

## Local development

Backend:

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp ../.env.example ../.env   # then fill in the relevant provider API key
uvicorn app.main:app --reload
```

Frontend:

```bash
cd client
npm install
npm run dev
```

Full stack (single container, mirrors production):

```bash
docker build -t lead-enquiry-agent .
docker run --env-file .env -p 8000:8000 lead-enquiry-agent
```

## Evaluation harness

**From the UI (recommended for assessors):** open the **Harness Testing** tab, choose
**Standard Evaluation** (15 enquiries) or **Manual Test Suite (E01–E14)**, pick 1× or 3×
repeats, and click **Run harness**. Progress, summary, charts, and per-run timelines appear
in the browser. Custom JSON upload is supported for hidden assessor datasets.

**From the CLI** (same `Pipeline.run()` as the API — see `docs/architecture.md` section 11):

```bash
PYTHONPATH=server python -m evaluation.harness   # defaults to 15 × 3 = 45 runs
PYTHONPATH=server python -m evaluation.adversarial
```

API surface: `GET /api/harness/datasets`, `POST /api/harness/jobs`, `GET /api/harness/jobs/{id}`,
`POST /api/harness/jobs/{id}/cancel`, `GET /api/harness/batches/{id}/dashboard`.
