# syntax=docker/dockerfile:1.9

# Stage 1: provide Rust toolchain (required by setup.py -> build_ov_cli_artifact -> cargo build)
# ragfs-python's default S3-enabled dependency set currently requires rustc >= 1.91.1.
FROM rust:1.91.1-trixie AS rust-toolchain

# Stage 2: build Python environment with uv (builds Rust CLI + C++ extension + web-studio from source)
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS py-builder

# Reuse Rust toolchain from stage 1 so setup.py can compile ov CLI in-place.
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
# Provide Node.js so setup.py build_py can build web-studio SPA in-tree.
COPY --from=node:24-trixie-slim /usr/local/bin/node /usr/local/bin/
COPY --from=node:24-trixie-slim /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ENV CARGO_HOME=/usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup
ENV PATH="/app/.venv/bin:/usr/local/cargo/bin:${PATH}"
ARG OPENVIKING_VERSION=
ARG TARGETPLATFORM
ARG UV_LOCK_STRATEGY=auto

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ccache \
    cmake \
    git \
 && rm -rf /var/lib/apt/lists/*

# Route gcc/g++/cc through ccache so cmake (which asks shutil.which("gcc")) picks
# up /usr/lib/ccache/gcc and benefits from the BuildKit cache mount on /root/.ccache.
ENV PATH="/usr/lib/ccache:${PATH}"
ENV CCACHE_DIR=/root/.ccache
# Pin Cargo's target dir to a stable path so a BuildKit cache mount can persist
# build artifacts across layer reruns even when uv builds the wheel in an
# ephemeral isolated tempdir.
ENV CARGO_TARGET_DIR=/cargo-target

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1
WORKDIR /app

# Copy source required for setup.py artifact builds and native extension build.
COPY Cargo.toml Cargo.lock ./
COPY pyproject.toml uv.lock setup.py README.md ./
COPY build_support/ build_support/
COPY bot/ bot/
COPY crates/ crates/
COPY openviking/ openviking/
COPY openviking_cli/ openviking_cli/
COPY src/ src/
COPY third_party/ third_party/
COPY web-studio/ web-studio/

# Install project and dependencies (triggers setup.py build_py → web-studio
# SPA build + build_ext → native extensions).
# Default to auto-refreshing uv.lock inside the ephemeral build context when it is
# stale, so Docker builds stay unblocked after dependency changes. Set
# UV_LOCK_STRATEGY=locked to keep fail-fast reproducibility checks.
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${TARGETPLATFORM} \
    --mount=type=cache,target=/root/.npm,id=npm-${TARGETPLATFORM} \
    --mount=type=cache,target=/cargo-target,id=cargo-target-${TARGETPLATFORM} \
    --mount=type=cache,target=/usr/local/cargo/registry,id=cargo-registry-${TARGETPLATFORM} \
    --mount=type=cache,target=/usr/local/cargo/git,id=cargo-git-${TARGETPLATFORM} \
    --mount=type=cache,target=/root/.ccache,id=ccache-${TARGETPLATFORM} \
    if [ -n "${OPENVIKING_VERSION:-}" ]; then \
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING="${OPENVIKING_VERSION}"; \
    elif [ -f openviking/_version.py ]; then \
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING="$(python -c "import runpy; print(runpy.run_path('openviking/_version.py')['version'])")"; \
    else \
        echo "OPENVIKING_VERSION build arg is required when building without openviking/_version.py" >&2; \
        exit 2; \
    fi; \
    case "${UV_LOCK_STRATEGY}" in \
        locked) \
            uv sync --locked --no-editable --extra bot --extra gemini \
            ;; \
        auto) \
            if ! uv lock --check; then \
                uv lock; \
            fi; \
            uv sync --locked --no-editable --extra bot --extra gemini \
            ;; \
        *) \
            echo "Unsupported UV_LOCK_STRATEGY: ${UV_LOCK_STRATEGY}" >&2; \
            exit 2 \
            ;; \
    esac

# Stage 3: runtime
FROM python:3.13-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=py-builder /app/.venv /app/.venv
COPY docker/openviking-entrypoint.sh /usr/local/bin/openviking-entrypoint
COPY docker/pending_health_server.py /usr/local/bin/openviking-pending-health
RUN mkdir -p /app/.openviking \
 && chmod +x /usr/local/bin/openviking-entrypoint /usr/local/bin/openviking-pending-health
ENV HOME="/app" \
    PATH="/app/.venv/bin:$PATH" \
    OPENVIKING_CONFIG_FILE="/app/.openviking/ov.conf" \
    OPENVIKING_CLI_CONFIG_FILE="/app/.openviking/ovcli.conf"

EXPOSE 1933

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:1933/health || exit 1

# All persistent state (ov.conf, ovcli.conf, workspace data) lives under
# /app/.openviking, which mirrors the host's ~/.openviking layout. Mount one
# volume there to persist everything across container restarts:
#   docker run -v ~/.openviking:/app/.openviking <image>
# If ov.conf is absent on first start, set OPENVIKING_CONF_CONTENT to the full
# JSON, or `docker exec` in and run `openviking-server init`.
# Override command to run CLI, e.g.:
# docker run --rm -v ~/.openviking:/app/.openviking <image> openviking --help
ENTRYPOINT ["openviking-entrypoint"]
