"""Pending health server for the OpenViking Docker entrypoint.

While the container is waiting for ``ov.conf`` to appear, the entrypoint
runs this tiny HTTP server on the same port the real OpenViking server
will bind. It answers *every* request — `/`, `/health`, anything — with
the same 503 JSON payload describing what's wrong and how to fix it, so
operators and agents probing the container can self-discover the issue
without having to read ``docker logs``.

This is intentionally undocumented in the user guide: it's a 防呆 (poka-yoke
/ fool-proofing) fallback that only runs when something has already gone
wrong (no ov.conf and no OPENVIKING_CONF_CONTENT). The happy path never
sees it, and pointing users at it would imply it's a deployment mode
worth depending on rather than a safety net. The response body itself is
self-explanatory when someone does hit it.
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 1933
_DEFAULT_CONFIG_FILE = "/app/.openviking/ov.conf"


def build_payload(config_file: str) -> dict:
    """Return the JSON body served on every route."""
    return {
        "status": "pending_initialization",
        "error": "OpenViking config file not found",
        "config_file": config_file,
        "fix": [
            "mount ~/.openviking on the host to /app/.openviking",
            "set OPENVIKING_CONF_CONTENT to the full ov.conf JSON",
            "docker exec into this container and run: openviking-server init",
        ],
    }


def make_handler(config_file: str) -> type[http.server.BaseHTTPRequestHandler]:
    body = (json.dumps(build_payload(config_file), ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self) -> None:
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_GET = _respond
        do_POST = _respond
        do_PUT = _respond
        do_PATCH = _respond
        do_DELETE = _respond
        do_HEAD = _respond
        do_OPTIONS = _respond

        def log_message(self, fmt: str, *args) -> None:
            sys.stdout.write("[openviking-pending-health] " + (fmt % args) + "\n")
            sys.stdout.flush()

    return Handler


class _ReusableServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve(host: str, port: int, config_file: str) -> None:
    handler = make_handler(config_file)
    with _ReusableServer((host, port), handler) as httpd:
        sys.stdout.write(
            f"[openviking-pending-health] serving on {host}:{port}; waiting for {config_file}\n"
        )
        sys.stdout.flush()
        httpd.serve_forever()


def main() -> int:
    host = os.environ.get("OPENVIKING_PENDING_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("OPENVIKING_PENDING_PORT", str(_DEFAULT_PORT)))
    config_file = os.environ.get("OPENVIKING_CONFIG_FILE", _DEFAULT_CONFIG_FILE)
    try:
        serve(host, port, config_file)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
