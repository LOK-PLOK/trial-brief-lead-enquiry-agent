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

# app/main.py looks for a "static" directory next to itself and, if
# present, serves it for any path not matched by /api/* routes.
COPY --from=client-build /client/dist ./app/static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Single uvicorn worker: appropriate for trial-scale traffic, and avoids
# multiple processes contending over the single SQLite file
# (docs/architecture.md section 15, SQLite concurrency risk).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
