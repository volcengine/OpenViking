#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

cd "$PROJECT_ROOT"

openapi-format "http://127.0.0.1:1933/openapi.json" --configFile "./script/gen-server-client/oaf-generate-conf.json"
node "./script/gen-server-client/polishOpId.js"
openapi-ts -i "./script/gen-server-client/generate/openapi-formatted.json" -o "./src/gen/ov-client" -c "@hey-api/client-axios"
