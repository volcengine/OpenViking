#!/bin/sh
set -eu

# A mounted worktree can reuse the native extension baked into the image.
# Python modules are then imported from the worktree, while the expensive C++
# engine build remains stable until its sources actually change.
if [ -n "${OPENVIKING_SOURCE_DIR:-}" ]; then
    engine_dir="${OPENVIKING_SOURCE_DIR}/openviking/storage/vectordb/engine"
    if [ ! -d "${engine_dir}" ]; then
        echo "OPENVIKING_SOURCE_DIR is not an OpenViking worktree: ${OPENVIKING_SOURCE_DIR}" >&2
        exit 2
    fi
    cp -a /opt/openviking-native-engine/. "${engine_dir}/"
    export PYTHONPATH="${OPENVIKING_SOURCE_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
    cd "${OPENVIKING_SOURCE_DIR}"
fi

exec "$@"
