"""Extra HTTP headers (e.g. Cloudflare Access service tokens) reach the wire.

An OpenViking server published behind an authenticating gateway (Cloudflare Access,
oauth2-proxy, an mTLS-terminating proxy) requires a client header on every request.
The JS side has carried this for a while (``OPENVIKING_EXTRA_HEADERS`` in
``memory-plugin-shared/lib/mcp-proxy-config.mjs``, plus ``extra_headers`` in the
linked ``ovcli.conf``); these tests pin the same behaviour for this plugin.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _start_server(records, *, require_header=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            records.append(dict(self.headers))
            if require_header and not self.headers.get(require_header):
                status, payload = 403, {"error": "blocked at edge"}
            else:
                status, payload = 200, {"status": "ok", "healthy": True,
                                        "version": "0.4.18", "auth_mode": "api_key"}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker


def test_extra_headers_from_env_reach_every_request(external_provider):
    _, _, module, _ = external_provider("extra-headers")
    records = []
    server, worker = _start_server(records, require_header="CF-Access-Client-Id")
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        settings = module._resolve_connection_settings({}, env={
            "OPENVIKING_API_KEY": "test-key",
            "OPENVIKING_EXTRA_HEADERS": json.dumps({
                "CF-Access-Client-Id": "id.access",
                "CF-Access-Client-Secret": "gateway-secret",
            }),
        })
        assert settings["extra_headers"] == {
            "CF-Access-Client-Id": "id.access",
            "CF-Access-Client-Secret": "gateway-secret",
        }
        client = module._VikingClient(endpoint, settings["api_key"],
                                      extra_headers=settings["extra_headers"])
        assert client.get("/health")["status"] == "ok"
        assert records[-1]["CF-Access-Client-Id"] == "id.access"
        assert records[-1]["CF-Access-Client-Secret"] == "gateway-secret"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_extra_headers_are_read_from_linked_ovcli_conf():
    """The documented client file already carries extra_headers; do not drop it."""
    import importlib.util
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("openviking_under_test", here / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    values = module._connection_values_from_ovcli({
        "url": "https://ov.example.com",
        "api_key": "user-key",
        "extra_headers": {"CF-Access-Client-Id": "id.access"},
    })
    assert values["extra_headers"] == {"CF-Access-Client-Id": "id.access"}


def test_extra_headers_cannot_forge_identity(external_provider):
    _, _, module, _ = external_provider("extra-headers-identity")
    parsed = module._parse_extra_headers(json.dumps({
        "X-OpenViking-Account": "someone-else",
        "X-OpenViking-User": "root",
        "CF-Access-Client-Id": "id.access",
    }))
    assert parsed == {"CF-Access-Client-Id": "id.access"}

    client = module._VikingClient("http://127.0.0.1:1", "real-key", extra_headers={
        "X-API-Key": "not-mine",
        "Authorization": "Bearer not-mine",
        "X-OpenViking-Actor-Peer": "not-mine",
        "CF-Access-Client-Id": "id.access",
    })
    headers = client._headers()
    assert headers["X-API-Key"] == "real-key"
    assert headers["Authorization"] == "Bearer real-key"
    assert "X-OpenViking-Account" not in headers
    assert headers["CF-Access-Client-Id"] == "id.access"


def test_extra_headers_invalid_input_is_ignored(external_provider):
    _, _, module, _ = external_provider("extra-headers-invalid")
    assert module._parse_extra_headers("not json") == {}
    assert module._parse_extra_headers("") == {}
    assert module._parse_extra_headers(None) == {}
    assert module._parse_extra_headers("[1, 2]") == {}
    assert module._parse_extra_headers({"A": {"nested": 1}, "B": 2, "C": True}) == {"B": "2"}
    settings = module._resolve_connection_settings({}, env={"OPENVIKING_EXTRA_HEADERS": "not json"})
    assert settings["extra_headers"] == {}
