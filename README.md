# Lead Enquiry Agent

A two-role agent system (Planner + Executor) with an independent LLM verifier gate, a
one-shot repair loop, and a reproducible evaluation harness — built for the Whisky Cask Club
technical trial.

Given a single block of unstructured lead-enquiry text, the system produces a verified,
structured lead record: an LLM Planner decides which tools to call and in what order, a
deterministic Executor carries out that plan via real tool/function calls, and a fully
independent LLM Verifier checks the result for fabrication and plan deviation before it is
accepted. On failure, a one-shot Repair pass may run; if verification still fails, the run is
quarantined rather than silently dropped.

The system runs end-to-end with [OpenRouter](https://openrouter.ai) as the LLM provider. See
[`docs/deployment.md`](docs/deployment.md) for environment variables, local setup, and Render
deployment.

## Documentation

| Document | Purpose |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | System design (pipeline, tools, verifier, harness, deployment shape) |
| [`docs/contracts.md`](docs/contracts.md) | Immutable input/output contracts |
| [`docs/deployment.md`](docs/deployment.md) | Environment, local/Docker/Render setup, troubleshooting |
| [`docs/testing.md`](docs/testing.md) | Unit, integration, harness, browser, API/Postman, adversarial testing |
| [`docs/proof_note.md`](docs/proof_note.md) | Trial deliverable: what was built, measured, and live URL check |
| [`docs/failure_log.md`](docs/failure_log.md) | Trial deliverable: what broke, what remains unfinished |
| [`docs/development-rules.md`](docs/development-rules.md) | Implementation rules for this codebase |
| [`docs/Trial_Brief_Paul_Detablan.md`](docs/Trial_Brief_Paul_Detablan.md) | Original assessment brief |

## Repository layout

```
server/       FastAPI backend: agent pipeline, tools, LLM adapters, DB, API
client/       React (Vite + TypeScript) single-page frontend
evaluation/   Standalone harness that drives evaluation + adversarial cases
docs/         Brief, architecture, contracts, deployment, testing, deliverables
```

## Local development

Backend:

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp ../.env.example ../.env   # then fill in OPENROUTER_API_KEY
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

**From the UI (recommended for assessors):** open the **Harness Testing** tab, choose a dataset
(Official Trial A Dataset E01–E15, Manual E01–E14, Adversarial, Custom, or Single Enquiry), pick 1× or 3×
repeats, and click **Run harness**. Progress, summary, charts, and per-run timelines appear in
the browser. Plain-text / JSON / CSV upload is supported for custom datasets.

**From the CLI** (same `Pipeline.run()` as the API — see `docs/architecture.md` section 11):

```bash
PYTHONPATH=server python -m evaluation.harness   # defaults to 15 × 3 = 45 runs
PYTHONPATH=server python -m evaluation.adversarial
```

See [`docs/testing.md`](docs/testing.md) for the full testing guide.
