FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NATIVE_TLS=true \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY openviking_llamaparse_bridge ./openviking_llamaparse_bridge
RUN useradd --create-home bridge

USER bridge

EXPOSE 8080

CMD ["python", "-m", "openviking_llamaparse_bridge"]
