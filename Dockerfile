# Production image: single container serving both the FastAPI JSON API and
# the built React static assets behind one public URL (docs/architecture.md
# section 13, confirmed single-container deployment direction).

# --- Stage 1: build the React client ---
FROM node:22-slim AS client-build
WORKDIR /client
COPY client/package*.json ./
RUN npm ci
COPY client/ ./
RUN npm run build

# --- Stage 2: the FastAPI server, with the client build baked in ---
FROM python:3.12-slim AS server

WORKDIR /app

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ .

# Evaluation fixtures + harness runner (used by the web Harness Testing job
# API). Kept separate from app/ so the CLI `python -m evaluation.harness`
# layout remains unchanged locally and in-container.
COPY evaluation/ ./evaluation/

# app/main.py looks for a "static" directory next to itself and, if
# present, serves it for any path not matched by /api/* routes.
COPY --from=client-build /client/dist ./app/static

# Sensible in-container defaults. Override DATABASE_URL on Render if a
# persistent disk is mounted (see docs/deployment.md).
ENV PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATABASE_URL=sqlite:////app/data/app.db \
    PYTHONPATH=/app

# Default published port; Render overrides via the PORT env var at runtime
# (scripts/start.sh reads it). EXPOSE is documentation only for Docker —
# the platform's own port mapping is what actually matters.
EXPOSE 8000

# Ensure the SQLite parent directory exists even when DATABASE_URL points
# somewhere other than the image's baked-in default.
RUN mkdir -p /app/data

COPY scripts/start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Single uvicorn worker: appropriate for trial-scale traffic, and avoids
# multiple processes contending over the single SQLite file
# (docs/architecture.md section 15, SQLite concurrency risk).
# PORT is expanded at runtime so this works on Render without a rebuild.
CMD ["/app/start.sh"]
