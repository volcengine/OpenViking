# syntax=docker/dockerfile:1.9

FROM python:3.12-slim-bookworm

ARG CUVS_PACKAGE="cuvs-cu13==26.6.0"
ARG CUPY_PACKAGE="cupy-cuda13x[ctk]==14.1.1"

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      cmake \
      git \
 && rm -rf /var/lib/apt/lists/*

# Keep Python and accelerator dependencies independent from the source layers.
# Python-only OpenViking changes therefore reuse this relatively expensive
# cuVS/CuPy installation.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install \
      --extra-index-url https://pypi.nvidia.com \
      "${CUVS_PACKAGE}" \
      "${CUPY_PACKAGE}" \
      "apscheduler>=3.11,<4" \
      "h5py>=3.12,<4" \
      "httpx>=0.25,<1" \
      "pydantic>=2,<3" \
      "pytest>=8,<9" \
      "pytest-asyncio>=0.24,<2" \
      "pyyaml>=6,<7" \
      "requests>=2.31,<3" \
      "xxhash>=3,<4"

# Pyxis/Enroot does not run the NVIDIA container-toolkit hook that normally
# discovers CUDA libraries installed by Python wheels. Register both the
# nvidia/*/lib directories and RAPIDS lib64 directories with the dynamic
# loader so cuVS works the same way under Docker and Pyxis.
RUN find /usr/local/lib/python*/site-packages/nvidia \
      -type d -name lib -print \
      > /etc/ld.so.conf.d/python-nvidia-wheels.conf \
 && find /usr/local/lib/python*/site-packages \
      -maxdepth 2 -type d -name lib64 -print \
      >> /etc/ld.so.conf.d/python-nvidia-wheels.conf \
 && ldconfig

WORKDIR /opt/openviking

# Compile the native VectorDB engine before copying the Python worktree. This
# layer is invalidated only by C++ or third-party source changes.
COPY src/ src/
COPY third_party/ third_party/
RUN cmake \
      -S /opt/openviking/src \
      -B /tmp/openviking-engine-build \
      -DCMAKE_BUILD_TYPE=Release \
      -DOV_PY_OUTPUT_DIR=/opt/openviking-native-engine \
      -DOV_PY_EXT_SUFFIX=.abi3.so \
      -DOV_X86_BUILD_VARIANTS='sse3;avx2;avx512' \
 && cmake --build /tmp/openviking-engine-build --config Release -j"$(nproc)" \
 && test -n "$(find /opt/openviking-native-engine -name '*.so' -print -quit)" \
 && rm -rf /tmp/openviking-engine-build

# Source changes in the Python integration layer are cheap to rebuild. A copy
# of the native engine is also retained for injection into mounted worktrees.
COPY openviking/ openviking/
COPY openviking_cli/ openviking_cli/
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
      "json-repair>=0.25" \
      "loguru>=0.7,<1" \
      "volcengine>=1.0.216"
COPY examples/cuvs_smoke.py /opt/openviking-cuvs/cuvs_smoke.py
COPY benchmark/cuvs/ /opt/openviking-cuvs-benchmark/
COPY docker/cuvs-dev/entrypoint.sh /usr/local/bin/openviking-cuvs-dev
RUN cp -a /opt/openviking-native-engine/. \
      /opt/openviking/openviking/storage/vectordb/engine/ \
 && chmod +x /usr/local/bin/openviking-cuvs-dev \
 && python -c "import cupy; from cuvs.neighbors import brute_force, cagra; import openviking.storage.vectordb.engine; print('cuVS development image import check passed')"

ENV PYTHONPATH="/opt/openviking"

ENTRYPOINT ["/usr/local/bin/openviking-cuvs-dev"]
CMD ["bash"]
