# cuVS development image

This lightweight image contains cuVS, CuPy, the OpenViking local VectorDB
native engine, and the minimal Python dependencies needed by the cuVS smoke
and vector benchmark harnesses. It deliberately excludes the full server,
bot, Web UI, and unrelated ingestion dependencies. It does not encode any
cluster-specific configuration.

Build it from the repository root:

```bash
docker build \
  -f docker/cuvs-dev/Dockerfile \
  -t openviking-cuvs:dev \
  .
```

The defaults track CUDA 13 packages. Build a CUDA 12 variant for systems whose
driver/runtime policy requires it:

```bash
docker build \
  --build-arg CUVS_PACKAGE=cuvs-cu12==26.6.0 \
  --build-arg 'CUPY_PACKAGE=cupy-cuda12x[ctk]==14.1.1' \
  -f docker/cuvs-dev/Dockerfile \
  -t openviking-cuvs:dev-cu12 \
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

Append `--dtype float16` to either smoke command to exercise the opt-in
lower-precision dataset and query path.

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

Set both `NVIDIA_VISIBLE_DEVICES` and `NVIDIA_DRIVER_CAPABILITIES` before
launching the SquashFS image, and explicitly pass them through when the Pyxis
configuration does not inherit host variables. This triggers the NVIDIA hook
that injects `libcuda` and the allocated devices:

```bash
export NVIDIA_VISIBLE_DEVICES=all
export NVIDIA_DRIVER_CAPABILITIES=compute,utility
```
