#!/bin/sh
set -eu

WITH_BOT="${OPENVIKING_WITH_BOT:-1}"
HEALTH_MAX_ATTEMPTS="${OPENVIKING_HEALTH_MAX_ATTEMPTS:-120}"
CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-/app/.openviking/ov.conf}"
PENDING_HEALTH_SCRIPT="/usr/local/bin/openviking-pending-health"
SERVER_PID=""
PENDING_PID=""

resolve_server_port() {
    SERVER_PORT="$(python - "${CONFIG_FILE}" <<'PY'
import json
import os
import sys

port = os.environ.get("OPENVIKING_SERVER_PORT", "").strip()
if not port and os.path.isfile(sys.argv[1]):
    with open(sys.argv[1], encoding="utf-8-sig") as config_file:
        config = json.loads(os.path.expandvars(config_file.read()))
    port = str((config.get("server") or {}).get("port", 1933)).strip()
port = port or "1933"
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit(f"invalid OpenViking server port: {port}")
print(port)
PY
    )"
    SERVER_HEALTH_URL="http://127.0.0.1:${SERVER_PORT}/health"
}

stop_pending_health() {
    if [ -n "${PENDING_PID}" ] && kill -0 "${PENDING_PID}" 2>/dev/null; then
        kill "${PENDING_PID}" 2>/dev/null || true
        wait "${PENDING_PID}" 2>/dev/null || true
    fi
    PENDING_PID=""
}

ensure_config() {
    if [ -f "${CONFIG_FILE}" ]; then
        return
    fi
    mkdir -p "$(dirname "${CONFIG_FILE}")"
    if [ -n "${OPENVIKING_CONF_CONTENT:-}" ]; then
        printf '%s' "${OPENVIKING_CONF_CONTENT}" > "${CONFIG_FILE}"
        echo "[openviking-entrypoint] wrote ${CONFIG_FILE} from OPENVIKING_CONF_CONTENT"
        return
    fi
    cat >&2 <<EOF
[openviking-entrypoint] ${CONFIG_FILE} not found.

To start OpenViking, do one of:
  - mount ~/.openviking on the host to /app/.openviking
  - set OPENVIKING_CONF_CONTENT to the full ov.conf JSON
  - docker exec into this container and run: openviking-server init

While waiting, every HTTP request to this container returns a 503 JSON
describing the problem and the fix above.
EOF
    resolve_server_port
    OPENVIKING_CONFIG_FILE="${CONFIG_FILE}" \
    OPENVIKING_PENDING_PORT="${SERVER_PORT}" \
        python "${PENDING_HEALTH_SCRIPT}" &
    PENDING_PID=$!
    trap 'stop_pending_health; exit 0' INT TERM

    while [ ! -f "${CONFIG_FILE}" ]; do
        if ! kill -0 "${PENDING_PID}" 2>/dev/null; then
            echo "[openviking-entrypoint] pending health server exited unexpectedly" >&2
            PENDING_PID=""
            exit 1
        fi
        sleep 5
    done

    stop_pending_health
    trap - INT TERM
    echo "[openviking-entrypoint] detected ${CONFIG_FILE}, starting OpenViking"
}

normalize_with_bot() {
    case "$1" in
        1|true|TRUE|yes|YES|on|ON)
            WITH_BOT="1"
            ;;
        0|false|FALSE|no|NO|off|OFF)
            WITH_BOT="0"
            ;;
        *)
            echo "[openviking-entrypoint] invalid OPENVIKING_WITH_BOT=${1}" >&2
            exit 2
            ;;
    esac
}

if [ "$#" -eq 1 ] && [ "$1" = "--healthcheck" ]; then
    resolve_server_port
    exec curl -fsS "${SERVER_HEALTH_URL}"
fi

if [ "$#" -gt 0 ]; then
    for arg in "$@"; do
        case "${arg}" in
            --with-bot)
                WITH_BOT="1"
                ;;
            --without-bot)
                WITH_BOT="0"
                ;;
            *)
                exec "$@"
                ;;
        esac
    done
fi

normalize_with_bot "${WITH_BOT}"
ensure_config
resolve_server_port

forward_signal() {
    if [ -n "${SERVER_PID}" ] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill "${SERVER_PID}" 2>/dev/null || true
    fi
}

trap 'forward_signal' INT TERM

SERVER_HOST="${OPENVIKING_SERVER_HOST:-0.0.0.0}"

if [ "${WITH_BOT}" = "1" ]; then
    openviking-server --host "${SERVER_HOST}" --port "${SERVER_PORT}" --with-bot &
else
    openviking-server --host "${SERVER_HOST}" --port "${SERVER_PORT}" &
fi
SERVER_PID=$!

attempt=0
until curl -fsS "${SERVER_HEALTH_URL}" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "[openviking-entrypoint] openviking-server exited before becoming healthy" >&2
        wait "${SERVER_PID}" || true
        exit 1
    fi
    if [ "${attempt}" -ge "${HEALTH_MAX_ATTEMPTS}" ]; then
        echo "[openviking-entrypoint] timed out waiting for ${SERVER_HEALTH_URL}" >&2
        forward_signal
        wait "${SERVER_PID}" || true
        exit 1
    fi
    sleep 1
done
echo "[openviking-entrypoint] openviking-server is healthy"

wait "${SERVER_PID}" || SERVER_STATUS=$?
exit "${SERVER_STATUS:-0}"
