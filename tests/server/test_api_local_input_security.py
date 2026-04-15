# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Security tests for HTTP server local input handling."""

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from openviking.parse.parsers.html import URLTypeDetector
from openviking.utils.network_guard import ensure_public_remote_target
from openviking_cli.exceptions import PermissionDeniedError


async def test_add_skill_accepts_temp_uploaded_file(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    skill_file = upload_temp_dir / "skill.md"
    skill_file.write_text(
        """---
name: uploaded-skill
description: temp uploaded skill
---

# Uploaded Skill
"""
    )

    resp = await client.post(
        "/api/v1/skills",
        json={"temp_file_id": skill_file.name, "wait": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"].startswith("viking://agent/skills/")


async def test_add_skill_rejects_direct_local_path(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={"data": "/app/ov.conf"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_skill_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={"temp_path": "upload_skill.md"},
    )
    assert resp.status_code == 422


async def test_add_skill_accepts_raw_skill_content(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={
            "data": """---
name: inline-skill
description: inline
---

# Inline Skill
"""
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"].startswith("viking://agent/skills/")


def _build_ovpack_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("pkg/_._meta.json", '{"uri": "viking://resources/pkg"}')
        zf.writestr("pkg/content.md", "# Demo\n")
    return buffer.getvalue()


@pytest.fixture
def loopback_http_url():
    body = b"<html><body>loopback secret</body></html>"

    class Handler(BaseHTTPRequestHandler):
        def _write_headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_HEAD(self):
            self._write_headers()

        def do_GET(self):
            self._write_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


async def test_import_ovpack_accepts_temp_uploaded_file(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    ovpack_file = upload_temp_dir / "demo.ovpack"
    ovpack_file.write_bytes(_build_ovpack_bytes())

    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": ovpack_file.name,
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"].startswith("viking://resources/imported/")


async def test_import_ovpack_rejects_direct_file_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "file_path": "/tmp/demo.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 422


async def test_import_ovpack_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_path": "upload_pack.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 422


async def test_import_ovpack_rejects_forged_temp_file_id(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    outside_file = upload_temp_dir.parent / "outside.ovpack"
    outside_file.write_bytes(_build_ovpack_bytes())

    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": "../outside.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"temp_path": "upload_resource.md", "reason": "legacy field"},
    )
    assert resp.status_code == 422


async def test_add_resource_rejects_loopback_remote_url(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "http://127.0.0.1:8765/", "reason": "ssrf probe"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"
    assert "public remote resource targets" in body["error"]["message"]


async def test_add_resource_rejects_private_git_ssh_url(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "git@127.0.0.1:org/repo.git", "reason": "internal git"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_url_detector_request_validator_blocks_loopback_head(loopback_http_url: str):
    detector = URLTypeDetector()

    with pytest.raises(PermissionDeniedError):
        await detector.detect(
            loopback_http_url,
            timeout=2.0,
            request_validator=ensure_public_remote_target,
        )
