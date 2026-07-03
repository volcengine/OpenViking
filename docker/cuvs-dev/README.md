# cuVS development image

This lightweight image contains cuVS, CuPy, the OpenViking local VectorDB
native engine, and the minimal Python dependencies needed by the cuVS smoke
and integration tests. It deliberately excludes the server, bot, Web UI, and
unrelated ingestion dependencies. It does not encode any cluster-specific
configuration.

Build it from the repository root:

```bash
docker build \
  -f docker/cuvs-dev/Dockerfile \
  -t openviking-cuvs:dev \
  .
```

Run the baked smoke test:

```bash
docker run --rm --gpus all openviking-cuvs:dev \
  python /opt/openviking-cuvs/cuvs_smoke.py
```

Exercise CAGRA with the same image:

```bash
docker run --rm --gpus all openviking-cuvs:dev \
  python /opt/openviking-cuvs/cuvs_smoke.py --algorithm cagra
```

For Python-only iteration, mount a worktree and point
`OPENVIKING_SOURCE_DIR` at it. The entrypoint copies the prebuilt native
engine into the mounted tree before running the command:

```bash
docker run --rm --gpus all \
  -v "$PWD:/workspace/OpenViking" \
  -e OPENVIKING_SOURCE_DIR=/workspace/OpenViking \
  openviking-cuvs:dev \
  python examples/cuvs_smoke.py
```

The dependency, native-engine, and Python-source layers are separate. Normal
changes to the Python cuVS integration reuse the dependency and C++ build
layers. Recompile the native layer only when `src/` or `third_party/` changes.

For an Enroot/Pyxis environment, export the already-built Docker image once to
a shared SquashFS file:

```bash
docker/cuvs-dev/export-sqsh.sh \
  openviking-cuvs:dev \
  /shared/images/openviking-cuvs-dev.sqsh
```

Set `NVIDIA_DRIVER_CAPABILITIES=compute,utility` before launching the SquashFS
image so the container runtime injects `libcuda` as well as management tools.
