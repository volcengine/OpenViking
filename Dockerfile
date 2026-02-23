# syntax=docker/dockerfile:1.9

# Stage 1: provide Go toolchain (required by setup.py -> build_agfs -> make build)
FROM golang:1.26-trixie AS go-toolchain

# Stage 2: build Python environment with uv (builds AGFS + C++ extension from source)
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS py-builder

# Reuse Go toolchain from stage 1 so setup.py can compile agfs-server in-place.
COPY --from=go-toolchain /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:${PATH}"
ARG OPENVIKING_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING=${OPENVIKING_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1
WORKDIR /app

# Copy source required for setup.py build_agfs() and CMake extension build.
COPY pyproject.toml uv.lock setup.py README.md ./
COPY openviking/ openviking/
COPY openviking_cli/ openviking_cli/
COPY src/ src/
COPY third_party/ third_party/

# Install project and dependencies (triggers setup.py build_agfs + build_extension).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-editable

# Stage 3: runtime
FROM python:3.13-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=py-builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV OPENVIKING_CONFIG_FILE="/app/ov.conf"

EXPOSE 1933

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:1933/health || exit 1

# Default runs server; override command to run CLI, e.g.:
# docker run --rm <image> -v "$HOME/.openviking/ovcli.conf:/root/.openviking/ovcli.conf" openviking --help
CMD ["openviking-server"]
