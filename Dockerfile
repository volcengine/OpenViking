# syntax=docker/dockerfile:1.9

# Stage 1: provide Rust toolchain (required by setup.py -> build_ov_cli_artifact -> cargo build)
# ragfs-python's default S3-enabled dependency set currently requires rustc >= 1.91.1.
FROM rust:1.91.1-trixie AS rust-toolchain

# Stage 2: build Python environment with uv (builds Rust CLI + C++ extension from source)
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS py-builder

# Reuse Rust toolchain from stage 1 so setup.py can compile ov CLI in-place.
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup
ENV PATH="/app/.venv/bin:/usr/local/cargo/bin:${PATH}"
ARG OPENVIKING_VERSION=
ARG TARGETPLATFORM
ARG UV_LOCK_STRATEGY=auto

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
 && rm -rf /var/lib/apt/lists/*

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

# Install project and dependencies (triggers setup.py artifact builds + build_extension).
# Default to auto-refreshing uv.lock inside the ephemeral build context when it is
# stale, so Docker builds stay unblocked after dependency changes. Set
# UV_LOCK_STRATEGY=locked to keep fail-fast reproducibility checks.
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${TARGETPLATFORM} \
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

# Build ragfs-python (Rust RAGFS binding) and extract the native extension
# into the installed openviking package.
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${TARGETPLATFORM} \
    uv pip install maturin && \
    export _TMPDIR=$(mktemp -d) && \
    trap 'rm -rf "$_TMPDIR"' EXIT && \
    cd crates/ragfs-python && \
    python -m maturin build --release --out "$_TMPDIR" && \
    cd ../.. && \
    export _OV_LIB=$(python -c "import openviking; from pathlib import Path; print(Path(openviking.__file__).resolve().parent / 'lib')") && \
    mkdir -p "$_OV_LIB" && \
    python - <<'PY'
import glob
import os
import sys
import zipfile

tmpdir = os.environ["_TMPDIR"]
ov_lib = os.environ["_OV_LIB"]
whls = glob.glob(os.path.join(tmpdir, "ragfs_python-*.whl"))
assert whls, "maturin produced no wheel"

with zipfile.ZipFile(whls[0]) as zf:
    for name in zf.namelist():
        bn = os.path.basename(name)
        if bn.startswith("ragfs_python") and (bn.endswith(".so") or bn.endswith(".pyd")):
            dst = os.path.join(ov_lib, bn)
            with zf.open(name) as src, open(dst, "wb") as f:
                f.write(src.read())
            os.chmod(dst, 0o755)
            print(f"ragfs-python: extracted {bn} -> {dst}")
            sys.exit(0)

print("WARNING: No ragfs_python .so/.pyd in wheel")
sys.exit(1)
PY

# Stage 3: runtime
FROM python:3.13-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=py-builder /app/.venv /app/.venv
COPY docker/openviking-console-entrypoint.sh /usr/local/bin/openviking-console-entrypoint
RUN chmod +x /usr/local/bin/openviking-console-entrypoint
ENV PATH="/app/.venv/bin:$PATH"
ENV OPENVIKING_CONFIG_FILE="/app/ov.conf"
ENV OPENVIKING_CLI_CONFIG_FILE="/app/ovcli.conf"

EXPOSE 1933 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:1933/health || exit 1

# Default runs server + console; override command to run CLI, e.g.:
# docker run --rm -v "$HOME/.openviking/ovcli.conf:/app/ovcli.conf" <image> openviking --help
ENTRYPOINT ["openviking-console-entrypoint"]
