#!/usr/bin/env bash
set -euo pipefail

MOONCAKE_ROOT=/opt/Mooncake
MOONCAKE_BUILD_DIR=${MOONCAKE_ROOT}/build

export MOONCAKE_BUILD_DIR
export MOONCAKE_STORE_LIB_DIR=${MOONCAKE_BUILD_DIR}/mooncake-store/src
export MOONCAKE_STORE_INCLUDE_DIR=${MOONCAKE_ROOT}/mooncake-store/include
export LD_LIBRARY_PATH="${MOONCAKE_BUILD_DIR}/mooncake-asio:\
${MOONCAKE_BUILD_DIR}/mooncake-common:\
${MOONCAKE_BUILD_DIR}/mooncake-common/src:\
${MOONCAKE_BUILD_DIR}/mooncake-common/etcd:\
${MOONCAKE_BUILD_DIR}/mooncake-store/src:\
${MOONCAKE_BUILD_DIR}/mooncake-store/src/cachelib_memory_allocator:\
${MOONCAKE_BUILD_DIR}/mooncake-transfer-engine/src:\
${MOONCAKE_BUILD_DIR}/mooncake-transfer-engine/src/common/base:\
${LD_LIBRARY_PATH:-}"

cleanup() {
    local exit_code=$?
    if [[ -n "${MASTER_PID:-}" ]]; then
        kill "${MASTER_PID}" 2>/dev/null || true
    fi
    if [[ -n "${METADATA_PID:-}" ]]; then
        kill "${METADATA_PID}" 2>/dev/null || true
    fi
    if [[ ${exit_code} -ne 0 ]]; then
        echo "=== Mooncake metadata log ==="
        cat /tmp/mooncake-metadata.log 2>/dev/null || true
        echo "=== Mooncake master log ==="
        cat /tmp/mooncake-master.log 2>/dev/null || true
    fi
    exit "${exit_code}"
}
trap cleanup EXIT

cd "${MOONCAKE_ROOT}/mooncake-transfer-engine/example/http-metadata-server-python"
python3 bootstrap_server.py >/tmp/mooncake-metadata.log 2>&1 &
METADATA_PID=$!

"${MOONCAKE_BUILD_DIR}/mooncake-store/src/mooncake_master" \
    --eviction_high_watermark_ratio=0.95 \
    --cluster_id=openviking_smoke \
    --port=50051 \
    >/tmp/mooncake-master.log 2>&1 &
MASTER_PID=$!

for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 \
        && kill -0 "${MASTER_PID}" 2>/dev/null; then
        break
    fi
    sleep 1
done

if ! kill -0 "${METADATA_PID}" 2>/dev/null || ! kill -0 "${MASTER_PID}" 2>/dev/null; then
    echo "Mooncake services failed to start" >&2
    exit 1
fi

echo "=== Official Mooncake Rust smoke ==="
cd "${MOONCAKE_ROOT}/mooncake-store/rust"
MC_RUST_STORE_RUN_INTEGRATION=true \
MC_METADATA_SERVER=http://127.0.0.1:8080/metadata \
MC_RUST_STORE_MASTER_ADDR=127.0.0.1:50051 \
MC_RUST_STORE_LOCAL_HOSTNAME=127.0.0.1 \
MC_RUST_STORE_PROTOCOL=tcp \
MC_RUST_STORE_DEVICE_NAME= \
cargo test --test minimal_smoke -- --nocapture

echo "=== OpenViking MooncakeProvider smoke ==="
cd /workspace/OpenViking
OPENVIKING_RUN_MOONCAKE_INTEGRATION=true \
MOONCAKE_LOCAL_HOSTNAME=127.0.0.1 \
MOONCAKE_METADATA_SERVER=http://127.0.0.1:8080/metadata \
MOONCAKE_MASTER_SERVER_ADDR=127.0.0.1:50051 \
MOONCAKE_PROTOCOL=tcp \
cargo test --locked -p ragfs-cache-mooncake \
    --features mooncake-native \
    --test native_smoke -- --nocapture
