# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# base: Python + uv
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/
WORKDIR /app

# ---------------------------------------------------------------------------
# deps: solo dependencias de producción (capa cacheable)
# ---------------------------------------------------------------------------
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------------------------------------------------------------------------
# dev: + herramientas de desarrollo; el código se monta como volumen (docker-compose)
# ---------------------------------------------------------------------------
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# runtime: imagen de producción (Render). Sin uv ni dev deps, usuario sin privilegios.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
