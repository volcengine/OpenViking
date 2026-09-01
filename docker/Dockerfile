# Patches the upstream OpenViking release image with the system dependency
# (libmagic) and Python binding (python-magic) required for content-based
# file classification in the vectorization pipeline.
#
# The base is pinned to the exact digest of the release currently deployed
# by deer-flow, rather than a mutable tag, so the patch always layers onto
# a known release. To rebump, replace the digest and rebuild.
FROM ghcr.io/volcengine/openviking@sha256:a46f792db367ec3a5c96260db5d9b3a40c308025bace82a6e0e6432a7d658a28

# The upstream image ships a uv-managed venv at /app/.venv without pip, so
# uv is installed into the base interpreter first and used to add the binding
# into the venv. libmagic1 provides the shared library python-magic binds to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/local/bin/python3.13 -m pip install --no-cache-dir "uv" \
    && uv pip install --python /app/.venv/bin/python --no-cache-dir "python-magic>=0.4.27" \
    && /app/.venv/bin/python - <<'PY'
import magic

# 8-byte PNG signature plus IHDR start: enough for libmagic, short of a full
# image (mirrors the smallest fixture in tests/fixtures/files).
data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
assert magic.from_buffer(data, mime=True) == "image/png"
PY
