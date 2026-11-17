# Build context: repository root (docker build -f backend/Dockerfile .)
FROM node:22-alpine AS ui
WORKDIR /ui/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/alembic.ini .
COPY backend/migrations ./migrations
COPY --from=ui /ui/backend/app/static ./app/static

RUN useradd --create-home --uid 10001 sentinel \
    && mkdir -p /app/data \
    && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
