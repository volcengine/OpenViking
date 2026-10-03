"""Installer-independent verification of the reviewed native package bytes."""

import hashlib
import importlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest


@pytest.fixture
def wheel_source():
    content = b"synthetic wheel contents"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/probe-1-py3-none-any.whl", content, requests
    server.shutdown()
    thread.join()
    server.server_close()


def test_wrong_native_hash_is_rejected_before_install(external_provider, tmp_path, wheel_source):
    _h, _p, module, _s = external_provider("hashes")
    packages = importlib.import_module(module.__name__ + ".local_packages")
    ql = importlib.import_module(module.__name__ + ".quick_local")
    url, _content, requests = wheel_source
    cache = tmp_path / "wheels"
    with pytest.raises(ql.QuickLocalSetupError, match="SHA-256"):
        packages.verified_requirements([f"probe @ {url}#sha256={'0' * 64}"], cache)
    assert requests == ["/probe-1-py3-none-any.whl"]
    assert not list(cache.rglob("*.whl"))


def test_verified_native_cache_is_rechecked_and_reused(external_provider, tmp_path, wheel_source):
    from packaging.requirements import Requirement

    _h, _p, module, _s = external_provider("hashes")
    packages = importlib.import_module(module.__name__ + ".local_packages")
    url, content, requests = wheel_source
    digest = hashlib.sha256(content).hexdigest()
    requirements = [
        f"probe[extra] @ {url}#sha256={digest}",
        'litellm==1.83.7; python_version >= "3.14"',
    ]
    cached = packages.verified_requirements(requirements, tmp_path / "wheels")
    parsed = Requirement(cached[0])
    assert parsed.extras == {"extra"}
    local = urlparse(parsed.url)
    assert local.scheme == "file"
    wheel = Path(unquote(local.path).lstrip("/") if os.name == "nt" else unquote(local.path))
    assert wheel.read_bytes() == content and cached[1] == requirements[1]
    assert packages.verified_requirements(requirements, tmp_path / "wheels") == cached
    assert len(requests) == 1
    wheel.write_bytes(b"corrupt cache")
    assert packages.verified_requirements(requirements, tmp_path / "wheels") == cached
    assert wheel.read_bytes() == content and len(requests) == 2


def test_bad_hash_never_reaches_pm(external_provider, tmp_path, wheel_source, monkeypatch):
    from unittest.mock import MagicMock

    pm = pytest.importorskip("pm.client")
    home, _p, module, _s = external_provider("hashes")
    packages = importlib.import_module(module.__name__ + ".local_packages")
    ql = importlib.import_module(module.__name__ + ".quick_local")
    url, _content, _requests = wheel_source
    monkeypatch.setattr(ql, "_pm_available", lambda: True)
    monkeypatch.setattr(ql, "openviking_install_satisfies_requirement", lambda _paths: False)
    monkeypatch.setattr(
        packages, "install_requirements", lambda **_kw: [f"probe @ {url}#sha256={'0' * 64}"]
    )
    install = MagicMock()
    monkeypatch.setattr(pm, "ensure_python_tool", install)
    engine = ql.QuickLocalSetup(health_check=lambda _url: (False, ""))
    with pytest.raises(ql.QuickLocalSetupError, match="SHA-256"):
        engine._ensure_openviking_installed(ql.managed_paths(home))
    install.assert_not_called()
    assert not (ql.managed_paths(home).root / "runtime-requirements.json").exists()
