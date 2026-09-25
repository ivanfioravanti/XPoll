# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin xpoll \
    && mkdir -p /data /backups /config \
    && chown xpoll:xpoll /data /backups \
    && chmod 700 /data /backups
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/poll.db \
    BACKUP_DIR=/backups \
    POLL_CONFIG=/config/poll.toml
WORKDIR /app
USER xpoll
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3)"]
# 0.0.0.0 is inside the container network only; compose publishes no host ports.
CMD ["pollctl", "serve", "--host", "0.0.0.0", "--port", "8787"]
