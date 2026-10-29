"""viking_write: deterministic in-place update of an existing file by exact URI.

The other write path, ``viking_remember``, submits to the session-extraction
pipeline (the server decides what is stored, where, and under which name) and
cannot guarantee a URI. ``viking_write`` covers the gap: replace/append/create
at ``POST /api/v1/content/write``. These tests pin the tool contract: the
payload that reaches the wire, the default mode, the argument guards that run
before any request, and that server failures surface as tool errors instead of
being swallowed.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _start_server(records, *, status=200, result_payload=None):
    """Local stand-in for the OpenViking content-write endpoint."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            records.append((self.path, self.rfile.read(length).decode("utf-8")))
            if status >= 400:
                # Real 404s carry no JSON envelope; leave the body empty so the
                # client falls back to the "HTTP 404" message.
                body = b""
            else:
                body = json.dumps(
                    {"result": result_payload if result_payload is not None else {}}
                ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker


def test_viking_write_replaces_existing_uri(external_provider):
    endpoint = None

    records = []
    server, worker = _start_server(
        records, result_payload={"content_updated": True, "written_bytes": 12}
    )
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("viking-write")
        client = module._VikingClient(endpoint, "test-key")
        provider = module.OpenVikingMemoryProvider()
        provider._client = client

        out = json.loads(
            provider.handle_tool_call(
                "viking_write",
                {
                    "uri": "viking://resources/wiki/entities/foo/foo.md",
                    "content": "updated body",
                    "mode": "replace",
                },
            )
        )

        assert out["status"] == "written"
        assert out["uri"].startswith("viking://resources/wiki/")
        assert out["mode"] == "replace"
        assert out["content_updated"] is True
        assert len(records) == 1
        path, body = records[0]
        assert path == "/api/v1/content/write"
        payload = json.loads(body)
        assert payload["content"] == "updated body"
        assert payload["mode"] == "replace"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_viking_write_default_mode_is_replace(external_provider):
    records = []
    server, worker = _start_server(records, result_payload={})
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("viking-write-default")
        client = module._VikingClient(endpoint, "test-key")
        provider = module.OpenVikingMemoryProvider()
        provider._client = client

        out = json.loads(
            provider.handle_tool_call(
                "viking_write", {"uri": "viking://resources/a/b.md", "content": "x"}
            )
        )

        assert out["mode"] == "replace"
        assert json.loads(records[0][1])["mode"] == "replace"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_viking_write_rejects_non_viking_uri(external_provider):
    records = []
    server, worker = _start_server(records)
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("viking-write-baduri")
        client = module._VikingClient(endpoint, "test-key")
        provider = module.OpenVikingMemoryProvider()
        provider._client = client

        out = json.loads(
            provider.handle_tool_call(
                "viking_write", {"uri": "/tmp/evil.md", "content": "x"}
            )
        )

        assert "error" in out
        assert records == [], "a non-viking:// URI must not reach the wire"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_viking_write_rejects_unknown_mode(external_provider):
    records = []
    server, worker = _start_server(records)
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("viking-write-badmode")
        client = module._VikingClient(endpoint, "test-key")
        provider = module.OpenVikingMemoryProvider()
        provider._client = client

        out = json.loads(
            provider.handle_tool_call(
                "viking_write",
                {"uri": "viking://resources/a/b.md", "content": "x", "mode": "upsert"},
            )
        )

        assert "error" in out
        assert "upsert" in out["error"]
        assert records == [], "an unknown mode must not reach the wire"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_viking_write_server_error_is_not_swallowed(external_provider):
    records = []
    server, worker = _start_server(records, status=404)
    endpoint = "http://127.0.0.1:%d" % server.server_port
    try:
        _, _, module, _ = external_provider("viking-write-404")
        client = module._VikingClient(endpoint, "test-key")
        provider = module.OpenVikingMemoryProvider()
        provider._client = client

        out = json.loads(
            provider.handle_tool_call(
                "viking_write",
                {"uri": "viking://resources/missing/x.md", "content": "x"},
            )
        )

        assert "error" in out
        assert "404" in out["error"]
        assert len(records) == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
