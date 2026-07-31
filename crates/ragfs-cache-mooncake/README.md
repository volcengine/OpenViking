# RAGFS Mooncake Cache Provider

This crate maps the RAGFS `CacheProvider` contract to Mooncake Store without
changing `CachedFileSystem`.

The optional official binding is pinned to Mooncake commit:

```text
1352bbec43081e461356aaecf6c70cddd826b455
```

Default tests use the same synchronous object boundary as the native binding,
so they run without Mooncake services or C++ libraries:

```bash
cargo test -p ragfs-cache-mooncake
```

## Native Build

Build the matching Mooncake checkout with Store Rust support:

```bash
cmake -S /path/to/Mooncake -B /path/to/Mooncake/build \
  -DWITH_STORE=ON \
  -DWITH_STORE_RUST=ON
cmake --build /path/to/Mooncake/build \
  --target build_mooncake_store_rust -j
```

Before building OpenViking, provide the paths expected by the official crate:

```bash
export MOONCAKE_BUILD_DIR=/path/to/Mooncake/build
export MOONCAKE_STORE_LIB_DIR=$MOONCAKE_BUILD_DIR/mooncake-store/src
export MOONCAKE_STORE_INCLUDE_DIR=/path/to/Mooncake/mooncake-store/include
export LD_LIBRARY_PATH="$MOONCAKE_STORE_LIB_DIR:$LD_LIBRARY_PATH"
```

Compile the adapter against the official binding:

```bash
cargo check -p ragfs-cache-mooncake --features mooncake-native
```

## TCP Smoke Test

With Mooncake Master and its metadata service running:

```bash
OPENVIKING_RUN_MOONCAKE_INTEGRATION=true \
MOONCAKE_LOCAL_HOSTNAME=127.0.0.1 \
MOONCAKE_METADATA_SERVER=http://127.0.0.1:8080/metadata \
MOONCAKE_MASTER_SERVER_ADDR=127.0.0.1:50051 \
MOONCAKE_PROTOCOL=tcp \
cargo test -p ragfs-cache-mooncake \
  --features mooncake-native \
  --test native_smoke -- --nocapture
```

The test writes one complete object, reads it back, removes it, verifies the
miss result, and closes the provider.

## Docker Smoke Test

The repository includes a Linux ARM64 image that builds the pinned Mooncake
commit, starts its HTTP metadata service and Master, runs Mooncake's official
Rust smoke test, and then runs the OpenViking provider smoke test:

```bash
docker build \
  -t openviking-mooncake-test:arm64 \
  docker/mooncake-test

docker run --rm --shm-size=4g \
  -v "$PWD:/workspace/OpenViking" \
  -v openviking-cargo-registry:/root/.cargo/registry \
  -v openviking-cargo-git:/root/.cargo/git \
  -v openviking-cargo-target:/workspace/OpenViking/target-docker \
  -e CARGO_TARGET_DIR=/workspace/OpenViking/target-docker \
  openviking-mooncake-test:arm64
```
