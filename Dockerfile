FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pinned dependencies first, so this layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps . \
 && useradd --system --uid 10001 --no-create-home app

# ---- migrate: one-off job that applies Alembic migrations, then exits ----
# Run before rolling out a new version; never at app startup (replicas would race).
FROM base AS migrate
COPY alembic.ini ./
COPY alembic ./alembic
USER app
CMD ["alembic", "upgrade", "head"]

# ---- runtime ----
FROM base AS runtime
USER app
EXPOSE 4002
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"4002\")}/health/live', timeout=2)" || exit 1
CMD ["python", "-m", "app"]
