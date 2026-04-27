"""Tests for the Docker pending health server.

The server lives under ``docker/`` (not a Python package), so we load it
via ``importlib`` rather than a normal import.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "docker" / "pending_health_server.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pending_health_server", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pending = _load_module()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_server():
    port = _free_port()
    config_file = "/tmp/test-ov.conf"
    handler_cls = pending.make_handler(config_file)
    server = pending._ReusableServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, config_file
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestPayload:
    def test_payload_shape(self):
        payload = pending.build_payload("/some/ov.conf")
        assert payload["status"] == "pending_initialization"
        assert payload["error"] == "OpenViking config file not found"
        assert payload["config_file"] == "/some/ov.conf"
        fixes = payload["fix"]
        assert isinstance(fixes, list) and len(fixes) == 3
        assert any("OPENVIKING_CONF_CONTENT" in line for line in fixes)
        assert any("openviking-server init" in line for line in fixes)
        assert any("/app/.openviking" in line for line in fixes)


class TestServer:
    @pytest.mark.parametrize("path", ["/", "/health", "/anything", "/deep/route?x=1"])
    def test_get_returns_503_with_same_payload(self, running_server, path):
        port, config_file = running_server
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=5)

        err = excinfo.value
        assert err.code == 503
        assert err.headers.get("Content-Type", "").startswith("application/json")
        body = json.loads(err.read())
        assert body == pending.build_payload(config_file)

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def test_other_methods_also_return_503(self, running_server, method):
        port, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health", data=b"", method=method
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=5)
        assert excinfo.value.code == 503

    def test_head_returns_503_without_body(self, running_server):
        port, _ = running_server
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", method="HEAD")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=5)
        assert excinfo.value.code == 503
        assert excinfo.value.read() == b""
