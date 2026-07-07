#!/bin/sh
set -eu

image=${1:-openviking-cuvs:dev}
output=${2:-openviking-cuvs-dev.sqsh}

if [ -e "${output}" ]; then
    echo "Output already exists: ${output}" >&2
    exit 2
fi

exec enroot import -o "${output}" "dockerd://${image}"
