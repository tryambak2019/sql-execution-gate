FROM node:22-bookworm-slim AS frontend-deps
WORKDIR /workspace/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM frontend-deps AS frontend-build
COPY frontend/ ./
RUN npm run build

FROM frontend-build AS frontend-test
RUN npm run lint

FROM python:3.12-slim AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/workspace/agents \
    SQL_EXECUTION_GATE_AGENTS_DIR=/workspace/agents
WORKDIR /workspace
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./agents/app
COPY config ./config
COPY server.py ./

FROM python-base AS backend-test
COPY tests/unit ./tests/unit
RUN uv sync --frozen --group dev --no-install-project \
    && .venv/bin/python -m pytest tests/unit -q

FROM python-base AS runtime
ENV PORT=8080
COPY --from=frontend-build /workspace/frontend/dist ./frontend/dist
USER 65532:65532
EXPOSE 8080
CMD ["sh", "-c", "exec .venv/bin/uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
