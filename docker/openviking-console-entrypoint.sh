#!/bin/sh
set -eu

SERVER_URL="http://127.0.0.1:1933"
SERVER_HEALTH_URL="${SERVER_URL}/health"
CONSOLE_PORT="${OPENVIKING_CONSOLE_PORT:-8020}"
CONSOLE_HOST="${OPENVIKING_CONSOLE_HOST:-0.0.0.0}"
WITH_BOT="${OPENVIKING_WITH_BOT:-1}"
SERVER_PID=""
CONSOLE_PID=""

normalize_with_bot() {
    case "$1" in
        1|true|TRUE|yes|YES|on|ON)
            WITH_BOT="1"
            ;;
        0|false|FALSE|no|NO|off|OFF)
            WITH_BOT="0"
            ;;
        *)
            echo "[openviking-console-entrypoint] invalid OPENVIKING_WITH_BOT=${1}" >&2
            exit 2
            ;;
    esac
}

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

forward_signal() {
    if [ -n "${SERVER_PID}" ] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill "${SERVER_PID}" 2>/dev/null || true
    fi
    if [ -n "${CONSOLE_PID}" ] && kill -0 "${CONSOLE_PID}" 2>/dev/null; then
        kill "${CONSOLE_PID}" 2>/dev/null || true
    fi
}

trap 'forward_signal' INT TERM

if [ "${WITH_BOT}" = "1" ]; then
    openviking-server --with-bot &
else
    openviking-server &
fi
SERVER_PID=$!

attempt=0
until curl -fsS "${SERVER_HEALTH_URL}" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "[openviking-console-entrypoint] openviking-server exited before becoming healthy" >&2
        wait "${SERVER_PID}" || true
        exit 1
    fi
    if [ "${attempt}" -ge 120 ]; then
        echo "[openviking-console-entrypoint] timed out waiting for ${SERVER_HEALTH_URL}" >&2
        forward_signal
        wait "${SERVER_PID}" || true
        exit 1
    fi
    sleep 1
done

python -m openviking.console.bootstrap \
    --host "${CONSOLE_HOST}" \
    --port "${CONSOLE_PORT}" \
    --openviking-url "${SERVER_URL}" &
CONSOLE_PID=$!

while kill -0 "${SERVER_PID}" 2>/dev/null && kill -0 "${CONSOLE_PID}" 2>/dev/null; do
    sleep 1
done

if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    wait "${SERVER_PID}" || SERVER_STATUS=$?
    SERVER_STATUS=${SERVER_STATUS:-1}
    forward_signal
    wait "${CONSOLE_PID}" || true
    exit "${SERVER_STATUS}"
fi

wait "${CONSOLE_PID}" || CONSOLE_STATUS=$?
CONSOLE_STATUS=${CONSOLE_STATUS:-0}
forward_signal
wait "${SERVER_PID}" || true
exit "${CONSOLE_STATUS}"
