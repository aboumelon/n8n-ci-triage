FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable \
    && useradd --create-home --uid 10001 triage \
    && mkdir -p /app/.local && chown triage:triage /app/.local
USER triage
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "ci_triage.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
