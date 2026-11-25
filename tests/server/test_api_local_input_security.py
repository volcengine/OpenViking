# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Security tests for HTTP server local input handling."""

import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from openviking.parse.accessors.http_accessor import URLTypeDetector
from openviking.server.temp_upload_store import TempUploadStore
from openviking.utils.network_guard import ensure_public_remote_target
from openviking_cli.exceptions import PermissionDeniedError
from tests.server.ovpack_test_helpers import build_ovpack_bytes


def _allow_admin_api_in_dev_mode(client: httpx.AsyncClient) -> None:
    # Admin routes require the app to have an API key manager, even in dev-mode tests.
    client._transport.app.state.api_key_manager = object()


async def test_add_skill_accepts_temp_uploaded_non_skill_filename(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    content = """---
name: uploaded-arbitrary-name
description: temp uploaded skill
---

# Uploaded Skill
"""
    upload = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("original-skill.md", content, "text/markdown")},
    )
    assert upload.status_code == 200, upload.text
    upload_id = upload.json()["result"]["temp_file_id"]

    resp = await client.post(
        "/api/v1/skills",
        json={"temp_file_id": upload_id, "wait": True},
    )
    assert resp.status_code == 200, resp.text
    root = resp.json()["result"]["root_uri"]
    assert root == "viking://user/default/skills/uploaded-arbitrary-name"
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/SKILL.md"})
    assert read.status_code == 200, read.text
    assert "# Uploaded Skill" in read.json()["result"]


async def test_add_skill_accepts_uploaded_zip_with_windows_separators(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    skill_zip = upload_temp_dir / "windows-skill.zip"
    with zipfile.ZipFile(skill_zip, "w") as zf:
        zf.writestr(
            "SKILL.md",
            """---
name: windows-skill
description: uploaded skill with Windows-style zip paths
---

# Windows Skill
""",
        )
        zf.writestr("scripts\\check_bounding_boxes.py", "print('ok')\n")

    resp = await client.post(
        "/api/v1/skills",
        json={"temp_file_id": skill_zip.name, "wait": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"

    script_uri = f"{body['result']['uri']}/scripts/check_bounding_boxes.py"
    read_resp = await client.get("/api/v1/content/read", params={"uri": script_uri})
    assert read_resp.status_code == 200, read_resp.text
    assert read_resp.json()["result"] == "print('ok')\n"


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
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_add_skill_accepts_raw_skill_content(client: httpx.AsyncClient):
    text = "Check the service status before changing its configuration. " * 10
    data = f"---\nname: inline-skill\ndescription: inline\n---\n\n# Inline Skill\n\n{text}\n"
    response = await client.post("/api/v1/skills", json={"data": data, "wait": True})

    assert response.status_code == 200, response.text
    root = response.json()["result"]["root_uri"]
    assert root == "viking://user/default/skills/inline-skill"
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/SKILL.md"})
    assert read.status_code == 200, read.text
    assert text.strip() in read.json()["result"]


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
    monkeypatch,
):
    _allow_admin_api_in_dev_mode(client)
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("demo.ovpack", build_ovpack_bytes(), "application/octet-stream")},
        data={"upload_mode": "shared"},
    )
    assert uploaded.status_code == 200, uploaded.text
    upload_id = uploaded.json()["result"]["temp_file_id"]
    assert upload_id.startswith("shared_")

    materialized: list[Path] = []
    original_resolve = TempUploadStore.resolve_for_consume

    async def record_local_copy(store, temp_file_id, ctx):
        resolved = await original_resolve(store, temp_file_id, ctx)
        path = Path(resolved.local_path)
        assert path.is_file()
        materialized.append(path)
        return resolved

    monkeypatch.setattr(TempUploadStore, "resolve_for_consume", record_local_copy)
    rejected = await client.post(
        "/api/v1/pack/import",
        json={"temp_file_id": upload_id, "parent": ""},
    )
    assert rejected.status_code == 400, rejected.text
    assert materialized == []

    imported = await client.post(
        "/api/v1/pack/import",
        json={"temp_file_id": upload_id, "parent": "viking://resources/imported"},
    )
    assert imported.status_code == 200, imported.text
    root = imported.json()["result"]["uri"]
    assert root == "viking://resources/imported/pkg"
    [local_copy] = materialized
    assert not local_copy.exists()
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/content.md"})
    assert read.status_code == 200, read.text
    assert read.json()["result"] == "# Demo\n"


async def test_import_ovpack_conflict_returns_structured_conflict(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    _allow_admin_api_in_dev_mode(client)
    ovpack_file = upload_temp_dir / "demo_conflict.ovpack"
    ovpack_file.write_bytes(build_ovpack_bytes())

    first = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": ovpack_file.name,
            "parent": "viking://resources/imported",
        },
    )
    assert first.status_code == 200

    ovpack_file.write_bytes(build_ovpack_bytes())
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": ovpack_file.name,
            "parent": "viking://resources/imported",
        },
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "CONFLICT"
    assert "Use on_conflict='overwrite'" in body["error"]["message"]
    assert body["error"]["details"]["resource"] == "viking://resources/imported/pkg"


async def test_import_ovpack_rejects_direct_file_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "file_path": "/tmp/demo.ovpack",
            "parent": "viking://resources/imported",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_import_ovpack_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_path": "upload_pack.ovpack",
            "parent": "viking://resources/imported",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_import_ovpack_rejects_removed_fields(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": "demo.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
            "force": True,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    validation_errors = body["error"]["details"]["validation_errors"]
    assert any("vectorize" in error["loc"] for error in validation_errors)
    assert any("force" in error["loc"] for error in validation_errors)


async def test_import_ovpack_rejects_forged_temp_file_id(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    outside_file = upload_temp_dir.parent / "outside.ovpack"
    outside_file.write_bytes(build_ovpack_bytes())

    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": "../outside.ovpack",
            "parent": "viking://resources/imported",
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
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_add_resource_rejects_loopback_remote_url(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "http://127.0.0.1:8765/", "reason": "ssrf probe"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_rejects_private_git_ssh_url(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "git@127.0.0.1:org/repo.git", "reason": "internal git"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_rejects_unverifiable_remote_dns(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "openviking.utils.network_guard._resolve_host_addresses",
        lambda _host: set(),
    )

    resp = await client.post(
        "/api/v1/resources",
        json={"path": "http://new-host.example.com/doc.md", "reason": "ssrf probe"},
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"
    assert "could not be resolved" in body["error"]["message"]


async def test_url_detector_request_validator_blocks_loopback_head(loopback_http_url: str):
    detector = URLTypeDetector()

    with pytest.raises(PermissionDeniedError):
        await detector.detect(
            loopback_http_url,
            timeout=2.0,
            request_validator=ensure_public_remote_target,
        )
