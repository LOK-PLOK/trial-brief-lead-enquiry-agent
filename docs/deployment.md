# Deployment

How to run the Lead Enquiry Agent locally, in Docker, and on [Render](https://render.com).
Architecture context: `docs/architecture.md` section 13.

**Two supported shapes (same app code):**

| Shape | When | Origins |
|---|---|---|
| **Single container** (Dockerfile) | Local Docker / Blueprint `render.yaml` | UI + `/api/*` same origin; no CORS |
| **Split Render services** (current live) | Separate Static Site + Web Service | Client uses `VITE_API_BASE_URL`; API allows `CORS_ORIGINS` |

**Live URLs (no login):**

- Frontend: https://trial-brief-lead-enquiry-agent-client.onrender.com
- Backend: https://trial-brief-lead-enquiry-agent.onrender.com (`GET /api/health`)

---

## Required environment variables

### Backend (`server` / API service)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | **Yes** (for real LLM calls) | _(none)_ | OpenRouter API key. Get one at https://openrouter.ai/keys |
| `MODEL_PROVIDER` | No | `openrouter` | Must be `openrouter` today (only concrete adapter). |
| `MODEL_NAME` | No | `openai/gpt-4o-mini` | OpenRouter model id (`vendor/model`). |
| `PLANNER_MODEL` / `EXTRACTOR_MODEL` / `VERIFIER_MODEL` | No | fall back to `MODEL_NAME` | Optional per-stage overrides. A distinct `VERIFIER_MODEL` is a documented partial mitigation for verifier independence. |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | Override only for proxies/tests. |
| `DATABASE_URL` | No | SQLite under `server/data/app.db` (local) or `/app/data/app.db` (container) | SQLAlchemy URL. Blank values are treated as unset. |
| `APP_ENV` | No | `development` | Set `production` in deploy. |
| `LOG_LEVEL` | No | `INFO` | Stdlib log level. |
| `PORT` | No (set by Render) | `8000` | HTTP listen port. Read by `scripts/start.sh`. |
| `CORS_ORIGINS` | Split deploy only | _(empty)_ | Comma-separated frontend origins (no trailing slash), e.g. `https://trial-brief-lead-enquiry-agent-client.onrender.com`. Leave blank for same-origin Docker / local Vite proxy. |

### Frontend (split Static Site build only)

| Variable | Required | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | Split deploy only | Backend public URL, no trailing slash (baked in at `npm run build`). Example: `https://trial-brief-lead-enquiry-agent.onrender.com`. Leave unset for same-origin Docker or Vite proxy. |

Copy `.env.example` → `.env` for local work. Never commit `.env`. See also `client/.env.example`.

---

## OpenRouter setup

1. Create an account at https://openrouter.ai and generate an API key.
2. Set `OPENROUTER_API_KEY` in `.env` (local) or as a Render secret on the **API** service.
3. Leave `MODEL_PROVIDER=openrouter`.
4. Optionally change `MODEL_NAME` to any OpenRouter model id (https://openrouter.ai/models). Structured-output reliability varies by model; the adapter uses instruct-then-validate-then-retry and retries once on schema failure.
5. Confirm credits/billing on OpenRouter before running the 45-run harness (~150+ LLM calls).

---

## Local setup

### Backend

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp ../.env.example ../.env
# Edit ../.env and set OPENROUTER_API_KEY=...
uvicorn app.main:app --reload
```

Health check: `curl http://127.0.0.1:8000/api/health` → `{"status":"ok"}`.

### Frontend (dev)

```bash
cd client
npm install
npm run dev
```

Vite proxies `/api` to the backend during local development (see `vite.config.ts`). You can instead set `VITE_API_BASE_URL=http://localhost:8000` in `client/.env` — then leave `CORS_ORIGINS` empty only if you still use the proxy; direct browser calls to `:8000` need the Vite origin listed in `CORS_ORIGINS`.

### Evaluation harness

Requires 15 enquiries in `evaluation/fixtures/enquiries.json` (brief §3.1). The Official Trial A Dataset (E01–E15) from `docs/WCC_Trial_A_Enquiry_Samples.md` is the primary fixture. The harness validates count by design rather than reporting empty metrics.

```bash
# from repo root
PYTHONPATH=server python -m evaluation.harness
PYTHONPATH=server python -m evaluation.adversarial
```

UI and API job runners are documented in [`testing.md`](testing.md).

---

## Production setup (Docker, single origin)

```bash
cp .env.example .env   # set OPENROUTER_API_KEY; leave CORS_ORIGINS blank
docker build -t lead-enquiry-agent .
docker run --env-file .env -p 8000:8000 lead-enquiry-agent
```

Verify:

```bash
curl http://127.0.0.1:8000/api/health
open http://127.0.0.1:8000/          # SPA
curl -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"enquiry_text":"Hi, Jane Doe jane@example.com +44 7700 900123 UK, interested in a cask, budget around 25k, urgent."}'
```

The image:

- Builds the React client (Node stage) with relative `/api` URLs (no `VITE_API_BASE_URL`).
- Installs Python deps and copies `server/` into `/app`.
- Bakes `client/dist` into `app/static` (served by FastAPI).
- Starts via `scripts/start.sh`, which binds `0.0.0.0:$PORT` (default 8000).

---

## Render deployment

### Current live layout (split services)

Used for the submission proof note: two Render services, no login.

1. **Web Service (API)** — Docker runtime, root `Dockerfile`, health check `/api/health`.
   - Env (minimum): `OPENROUTER_API_KEY`, `MODEL_PROVIDER=openrouter`, `MODEL_NAME=openai/gpt-4o-mini`, `APP_ENV=production`, `DATABASE_URL=sqlite:////app/data/app.db`, `CORS_ORIGINS=https://trial-brief-lead-enquiry-agent-client.onrender.com`.
2. **Static Site (client)** — build `cd client && npm install && npm run build`, publish `client/dist`.
   - Build env: `VITE_API_BASE_URL=https://trial-brief-lead-enquiry-agent.onrender.com` (must match the API public URL; bake at build time).

After changing either URL, update both `VITE_API_BASE_URL` (rebuild client) and `CORS_ORIGINS` (redeploy API).

Cold-network check: open the **frontend** URL from a phone hotspot; confirm health via the **backend** URL.

### Option A — Blueprint single container (`render.yaml`)

One Web Service serving UI + API from the Docker image (same-origin; leave `CORS_ORIGINS` blank).

1. Push this repo to GitHub.
2. In Render: **New → Blueprint** → select the repo.
3. Set the `OPENROUTER_API_KEY` secret when prompted (`sync: false` in `render.yaml`).
4. Deploy. Health check path is `/api/health`. Public URL is both SPA and API.

### Option B — Manual single-container Web Service

1. **New → Web Service** → connect the repo.
2. Runtime: **Docker**. Dockerfile path: `./Dockerfile`.
3. Health check path: `/api/health`.
4. Environment variables (at minimum):

   | Key | Value |
   |---|---|
   | `OPENROUTER_API_KEY` | _(secret)_ |
   | `MODEL_PROVIDER` | `openrouter` |
   | `MODEL_NAME` | `openai/gpt-4o-mini` |
   | `APP_ENV` | `production` |
   | `DATABASE_URL` | `sqlite:////app/data/app.db` |

5. Deploy. One public URL; no login.

### SQLite persistence on Render

Free-tier filesystem is ephemeral: restarts wipe `/app/data/app.db`. For durable runs/leads:

1. Add a Render Disk, mount path e.g. `/var/data`.
2. Set `DATABASE_URL=sqlite:////var/data/app.db`.

Without a disk the app still works; history simply resets on redeploy.

### PORT

Do **not** hardcode a listen port in the Render dashboard. Render injects `PORT`; `scripts/start.sh` already reads it.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `500` on `POST /api/runs` mentioning OpenRouter / API key | Missing `OPENROUTER_API_KEY` | Set the env var / secret on the API service and redeploy. |
| Browser CORS / network errors from the Static Site | Missing or wrong `CORS_ORIGINS`, or stale `VITE_API_BASE_URL` | Set `CORS_ORIGINS` to the exact frontend origin; rebuild client with correct `VITE_API_BASE_URL`. |
| Frontend calls wrong host / relative `/api` 404 on Static Site | `VITE_API_BASE_URL` unset at build time | Set build env and redeploy the Static Site. |
| `Could not parse SQLAlchemy URL` | Blank `DATABASE_URL=` treated as empty before the blank-string fix | Use current `core/config.py` (blank → default), or omit `DATABASE_URL` entirely. |
| Frontend blank / 404 on refresh (Docker single origin) | Static assets missing from image | Rebuild image; confirm Docker copies `client/dist` → `app/static`. |
| Health check failing on Render | Wrong path or app crash on boot | Path must be `/api/health` on the **API** service. Check Render logs. |
| Cost always `$0.00` | Model not in `llm/pricing.py` | Add a per-1K entry for your `MODEL_NAME`, or accept the honest 0.0 warning. |
| Harness refuses to start | Empty `evaluation/fixtures/enquiries.json` | Supply the 15 brief samples; harness validates count by design. |
| Rate limits mid-harness | OpenRouter throttling | Resume with the same `harness_batch_id` — harness is resumable. |
| Duplicate lead rejected | Expected | `write_record` returns `error="duplicate"`; UI shows the duplicate banner. |

---

## Harness entry points

- **UI (recommended for assessors):** **Harness Testing** tab — start jobs, watch progress, open summary/charts/run detail. See [`testing.md`](testing.md).
- **CLI:** `PYTHONPATH=server python -m evaluation.harness` and `python -m evaluation.adversarial` (same `Pipeline.run()` as the API).
- **API:** `POST /api/harness/jobs`, progress/cancel endpoints, and `GET /api/harness/batches/{id}/dashboard` (plus read endpoints for summary/runs).

The Repair Loop (`docs/architecture.md` section 10) is implemented: one repair attempt after verifier failure, then completed or quarantined. Repair never calls `write_record`.

---

## Submission ops checklist

1. Deploy API + client (split or single-container); set `OPENROUTER_API_KEY` (and split-deploy `CORS_ORIGINS` / `VITE_API_BASE_URL` as needed).
2. Cold-network check of the public frontend URL; keep URLs/timestamp in [`proof_note.md`](proof_note.md).
3. Run a live harness (UI **1× Standard** or CLI) and optionally adversarial; keep reports under `evaluation/reports/`.
4. Re-run the Official Trial A Dataset (E01–E15) harness for the reproducibility gate after deploy or model changes.
