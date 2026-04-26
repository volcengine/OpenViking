#!/bin/sh
set -eu

SERVER_URL="http://127.0.0.1:1933"
SERVER_HEALTH_URL="${SERVER_URL}/health"
CONSOLE_PORT="${OPENVIKING_CONSOLE_PORT:-8020}"
CONSOLE_HOST="${OPENVIKING_CONSOLE_HOST:-0.0.0.0}"
WITH_BOT="${OPENVIKING_WITH_BOT:-1}"
CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-/app/conf/ov.conf}"
DATA_DIR="${OPENVIKING_DATA_DIR:-/app/data}"
CONFIG_CONTENT="${OPENVIKING_CONF_CONTENT:-}"
CONFIG_WAIT_SECONDS="${OPENVIKING_CONFIG_WAIT_SECONDS:-5}"
SERVER_PID=""
CONSOLE_PID=""
PENDING_PID=""

export OPENVIKING_CONFIG_FILE="${CONFIG_FILE}"
export OPENVIKING_DATA_DIR="${DATA_DIR}"

stop_pending_server() {
    if [ -n "${PENDING_PID}" ] && kill -0 "${PENDING_PID}" 2>/dev/null; then
        kill "${PENDING_PID}" 2>/dev/null || true
        wait "${PENDING_PID}" 2>/dev/null || true
    fi
    PENDING_PID=""
}

print_init_instructions() {
    cat >&2 <<EOF
[openviking-console-entrypoint] OpenViking is waiting for initialization.

Required persistent paths:
  config: ${CONFIG_FILE}
  data:   ${DATA_DIR}

Mount persistent storage for both paths, then initialize the config inside the container:
  mkdir -p "$(dirname "${CONFIG_FILE}")" "${DATA_DIR}"
  openviking-server init

Alternatively, set OPENVIKING_CONF_CONTENT to the full ov.conf JSON content.
If your platform supports only one persistent volume, mount it at ${DATA_DIR}
and set OPENVIKING_CONFIG_FILE=${DATA_DIR}/conf/ov.conf.
If you create ov.conf manually, set storage.workspace to "${DATA_DIR}".
This container exposes a pending /health endpoint until ${CONFIG_FILE} exists.
EOF
}

write_config_from_env() {
    if [ -f "${CONFIG_FILE}" ] || [ -z "${CONFIG_CONTENT}" ]; then
        return 0
    fi

    CONFIG_TARGET="${CONFIG_FILE}" python - <<'PY'
import json
import os
from pathlib import Path

config_path = Path(os.environ["CONFIG_TARGET"])
content = os.environ["OPENVIKING_CONF_CONTENT"]

try:
    parsed = json.loads(content)
except json.JSONDecodeError as exc:
    raise SystemExit(
        f"[openviking-console-entrypoint] OPENVIKING_CONF_CONTENT must be valid JSON: {exc}"
    )

if not isinstance(parsed, dict):
    raise SystemExit(
        "[openviking-console-entrypoint] OPENVIKING_CONF_CONTENT must be a JSON object"
    )

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(
    f"[openviking-console-entrypoint] wrote {config_path} from OPENVIKING_CONF_CONTENT",
    flush=True,
)
PY
}

start_pending_http_server() {
    python - <<'PY'
import http.server
import json
import os
import socketserver

host = os.environ.get("OPENVIKING_PENDING_HEALTH_HOST", "0.0.0.0")
port = int(os.environ.get("OPENVIKING_PENDING_HEALTH_PORT", "1933"))
config_file = os.environ["OPENVIKING_CONFIG_FILE"]
data_dir = os.environ["OPENVIKING_DATA_DIR"]


def payload():
    return {
        "status": "pending_initialization",
        "message": "OpenViking is waiting for persistent config initialization.",
        "config_file": config_file,
        "data_dir": data_dir,
        "next_steps": [
            "Mount persistent storage for the config and data paths.",
            f"If only one persistent volume is available, mount it at {data_dir} "
            f"and set OPENVIKING_CONFIG_FILE={data_dir}/conf/ov.conf.",
            "Enter the container and run: openviking-server init",
            f"Ensure ov.conf has storage.workspace set to {data_dir}.",
        ],
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, status_code):
        body = json.dumps(payload(), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in {"/", "/health"}:
            self._send_json(200)
            return
        if path == "/ready":
            self._send_json(503)
            return
        self._send_json(404)

    def log_message(self, fmt, *args):
        print("[openviking-pending-health] " + fmt % args, flush=True)


class TCPServer(socketserver.TCPServer):
    allow_reuse_address = True


with TCPServer((host, port), Handler) as httpd:
    print(
        f"[openviking-pending-health] serving /health on {host}:{port}; "
        f"waiting for {config_file}",
        flush=True,
    )
    httpd.serve_forever()
PY
}

wait_for_config() {
    mkdir -p "$(dirname "${CONFIG_FILE}")" "${DATA_DIR}"
    write_config_from_env
    if [ -f "${CONFIG_FILE}" ]; then
        return
    fi

    print_init_instructions
    start_pending_http_server &
    PENDING_PID=$!
    trap 'stop_pending_server; exit 0' INT TERM

    while [ ! -f "${CONFIG_FILE}" ]; do
        if ! kill -0 "${PENDING_PID}" 2>/dev/null; then
            echo "[openviking-console-entrypoint] pending /health server exited unexpectedly" >&2
            exit 1
        fi
        sleep "${CONFIG_WAIT_SECONDS}"
    done

    echo "[openviking-console-entrypoint] found ${CONFIG_FILE}; starting OpenViking"
    stop_pending_server
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
wait_for_config

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
