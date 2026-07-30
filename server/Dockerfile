# Standalone server-only image (API without the built React app served
# alongside it). Useful for local iteration / running the API against a
# separately-served frontend. For the production single-container build that
# bundles the React app too, see the multi-stage Dockerfile at the repo root
# (docs/architecture.md section 13).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Single uvicorn worker: appropriate for trial-scale traffic, and avoids
# multiple processes contending over the single SQLite file
# (docs/architecture.md section 15, SQLite concurrency risk).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
