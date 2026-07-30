# Lead Enquiry Agent

A two-role agent system (Planner + Executor) with an independent LLM verifier gate and a
reproducible evaluation harness, built for the Whisky Cask Club technical trial.

Given a single block of unstructured lead-enquiry text, the system produces a verified,
structured lead record: an LLM Planner decides which tools to call and in what order, a
deterministic Executor carries out that plan via real tool/function calls, and a fully
independent LLM Verifier checks the result for fabrication and plan deviation before it is
accepted. On failure, one repair pass is attempted before the record is quarantined.

This is a scaffold. Business logic is intentionally not implemented yet — see `TODO` markers
throughout the codebase and `docs/development-rules.md` for the rules governing how it should
be filled in.

## Documentation

- [`docs/Trial_Brief_Paul_Detablan.md`](docs/Trial_Brief_Paul_Detablan.md) — the original brief.
- [`docs/architecture.md`](docs/architecture.md) — source of truth for system design.
- [`docs/development-rules.md`](docs/development-rules.md) — source of truth for implementation rules.
- [`docs/api.md`](docs/api.md) — API contract (to be filled in as endpoints are implemented).
- [`docs/database.md`](docs/database.md) — database schema reference.
- [`docs/decisions.md`](docs/decisions.md) — stack/model choice justifications.

## Repository layout

```
server/       FastAPI backend: agent pipeline, tools, LLM adapters, DB, API
client/       React (Vite + TypeScript) single-page frontend
evaluation/   Standalone harness that drives the 45-run evaluation + adversarial cases
docs/         Architecture, API, database, and decision documentation
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

Run from the repo root, with the `server` package importable (the harness calls the exact
same `Pipeline.run()` used by the API — see `docs/architecture.md` section 11):

```bash
PYTHONPATH=server python -m evaluation.harness   # 15 enquiries x 3 repeats = 45 runs
PYTHONPATH=server python -m evaluation.adversarial
```

See [`docs/architecture.md`](docs/architecture.md) section 11 for what is measured.
