FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5050 \
    LISA_PRO_DATA=/data

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN mkdir -p /data && chown -R 10001:10001 /app /data
USER 10001:10001

EXPOSE 5050
VOLUME ["/data"]

ENTRYPOINT ["/app/.venv/bin/lisa-pro-ui"]
