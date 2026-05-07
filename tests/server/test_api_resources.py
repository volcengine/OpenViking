# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for resource management endpoints."""

from types import SimpleNamespace
import zipfile

import httpx

from openviking.telemetry import get_current_telemetry


def _patch_resources_config(monkeypatch, upload_temp_dir, *, allow_local_path: bool) -> None:
    config = SimpleNamespace(
        allow_local_path=allow_local_path,
        storage=SimpleNamespace(get_upload_temp_dir=lambda: upload_temp_dir),
    )
    monkeypatch.setattr(
        "openviking.server.routers.resources.get_openviking_config",
        lambda: config,
    )


def _snapshot_directory(dir_path):
    snapshot = {}
    for file_path in sorted(dir_path.rglob("*")):
        if file_path.is_file():
            relative = str(file_path.relative_to(dir_path))
            stat = file_path.stat()
            snapshot[relative] = (stat.st_size, stat.st_mtime_ns, file_path.read_bytes())
    return snapshot


async def test_add_resource_success(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test resource",
            "wait": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "time" not in body
    assert "usage" not in body
    assert "telemetry" not in body
    assert "root_uri" in body["result"]
    assert body["result"]["root_uri"].startswith("viking://")


async def test_add_resource_with_wait(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test resource",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]


async def test_add_resource_with_telemetry_wait(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "telemetry resource",
            "wait": True,
            "telemetry": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    telemetry_summary = body["telemetry"]["summary"]
    assert telemetry_summary["operation"] == "resources.add_resource"
    assert "usage" not in body
    semantic = telemetry_summary.get("semantic_nodes")
    if semantic is not None:
        assert semantic["total"] is None or semantic["done"] == semantic["total"]
        assert semantic.get("pending") in (None, 0)
        assert semantic.get("running") in (None, 0)
    assert "resource" in telemetry_summary
    assert "memory" not in telemetry_summary


async def test_add_resource_with_telemetry_includes_resource_breakdown(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
    upload_temp_dir,
):
    async def fake_add_resource(**kwargs):
        telemetry = get_current_telemetry()
        telemetry.set("resource.request.duration_ms", 152.3)
        telemetry.set("resource.process.duration_ms", 101.7)
        telemetry.set("resource.parse.duration_ms", 38.1)
        telemetry.set("resource.parse.warnings_count", 1)
        telemetry.set("resource.finalize.duration_ms", 22.4)
        telemetry.set("resource.summarize.duration_ms", 31.8)
        telemetry.set("resource.wait.duration_ms", 46.9)
        telemetry.set("resource.watch.duration_ms", 0.8)
        telemetry.set("resource.flags.wait", True)
        telemetry.set("resource.flags.build_index", True)
        telemetry.set("resource.flags.summarize", False)
        telemetry.set("resource.flags.watch_enabled", False)
        return {
            "status": "success",
            "root_uri": "viking://resources/demo",
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    demo_file = upload_temp_dir / "demo.md"
    demo_file.write_text("# demo\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": demo_file.name,
            "reason": "telemetry resource",
            "wait": True,
            "telemetry": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    resource = body["telemetry"]["summary"]["resource"]
    assert resource["request"]["duration_ms"] == 152.3
    assert resource["process"]["parse"] == {"duration_ms": 38.1, "warnings_count": 1}
    assert resource["wait"]["duration_ms"] == 46.9
    assert resource["flags"] == {
        "wait": True,
        "build_index": True,
        "summarize": False,
        "watch_enabled": False,
    }


async def test_add_resource_business_error_uses_error_envelope(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_add_resource(**kwargs):
        return {
            "status": "error",
            "errors": ["Parse error: boom"],
            "source_path": kwargs["path"],
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": "https://example.com/bad.md",
            "reason": "test resource",
        },
    )

    assert resp.status_code == 500
    body = resp.json()
    assert body["status"] == "error"
    assert "result" not in body
    assert body["error"]["code"] == "PROCESSING_ERROR"
    assert body["error"]["message"] == "Parse error: boom"


async def test_add_skill_business_error_uses_error_envelope(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_add_skill(**kwargs):
        return {
            "status": "error",
            "errors": [{"message": "Skill parse error: boom"}],
        }

    monkeypatch.setattr(service.resources, "add_skill", fake_add_skill)

    resp = await client.post(
        "/api/v1/skills",
        json={"data": {"name": "bad-skill"}},
    )

    assert resp.status_code == 500
    body = resp.json()
    assert body["status"] == "error"
    assert "result" not in body
    assert body["error"]["code"] == "PROCESSING_ERROR"
    assert body["error"]["message"] == "Skill parse error: boom"


async def test_add_resource_with_summary_only_telemetry(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "summary only telemetry resource",
            "wait": True,
            "telemetry": {"summary": True},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "summary" in body["telemetry"]
    assert "usage" not in body
    assert "events" not in body["telemetry"]
    assert "truncated" not in body["telemetry"]
    assert "dropped" not in body["telemetry"]


async def test_add_resource_rejects_events_only_telemetry(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "events only telemetry",
            "wait": False,
            "telemetry": {"summary": False, "events": True},
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "events" in body["error"]["message"]


async def test_add_resource_file_not_found(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "/nonexistent/file.txt", "reason": "test"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_with_to(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "to": "viking://resources/custom/sample",
            "reason": "test resource",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "custom" in body["result"]["root_uri"]


async def test_add_resource_with_resources_root_to_uses_child_uri(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    archive_path = upload_temp_dir / "tt_b.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": archive_path.name,
            "to": "viking://resources",
            "reason": "test resource root import",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/tt_b"


async def test_add_resource_with_resources_root_to_trailing_slash_uses_child_uri(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    archive_path = upload_temp_dir / "tt_b.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": archive_path.name,
            "to": "viking://resources/",
            "reason": "test resource root import trailing slash",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/tt_b"


async def test_add_resource_with_resources_root_to_keeps_single_file_directory(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    file_path = upload_temp_dir / "upload_temp.txt"
    file_path.write_text("hello world\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": file_path.name,
            "source_name": "aa.txt",
            "to": "viking://resources",
            "reason": "test resource root file import",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/aa"


async def test_add_resource_with_resources_root_to_trailing_slash_keeps_single_file_directory(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    file_path = upload_temp_dir / "upload_temp.txt"
    file_path.write_text("hello world\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": file_path.name,
            "source_name": "aa.txt",
            "to": "viking://resources/",
            "reason": "test resource root file import trailing slash",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/aa"


async def test_wait_processed_empty_queue(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 30.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_wait_processed_after_add(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    await client.post(
        "/api/v1/resources",
        json={"temp_file_id": sample_markdown_file.name, "reason": "test"},
    )
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 60.0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_add_resource_with_watch_interval_requires_to(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test resource with watch interval",
            "watch_interval": 5.0,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "watch_interval > 0 requires 'to' to be specified" in body["error"]["message"]


async def test_add_resource_with_default_watch_interval(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test resource with default watch interval",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]


async def test_temp_upload_success(client: httpx.AsyncClient, upload_temp_dir):
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("sample.md", b"# upload\n", "text/markdown")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "telemetry" not in body
    assert body["result"]["temp_file_id"].endswith(".md")
    assert "/" not in body["result"]["temp_file_id"]


async def test_temp_upload_with_telemetry_returns_summary(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("sample.md", b"# upload\n", "text/markdown")},
        data={"telemetry": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["temp_file_id"].endswith(".md")
    assert "/" not in body["result"]["temp_file_id"]
    assert body["telemetry"]["summary"]["operation"] == "resources.temp_upload"


async def test_add_resource_rejects_direct_local_path(
    client: httpx.AsyncClient,
    upload_temp_dir,
    monkeypatch,
):
    _patch_resources_config(monkeypatch, upload_temp_dir, allow_local_path=False)
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "/app/ov.conf", "reason": "security test"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_accepts_local_file_path_when_enabled(
    client: httpx.AsyncClient,
    temp_dir,
    upload_temp_dir,
    monkeypatch,
):
    _patch_resources_config(monkeypatch, upload_temp_dir, allow_local_path=True)
    source_file = temp_dir / "source.md"
    source_file.write_text("# local file\n\nhello\n")
    before_content = source_file.read_text()
    before_mtime = source_file.stat().st_mtime_ns

    resp = await client.post(
        "/api/v1/resources",
        json={"path": str(source_file), "reason": "local file path"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"].startswith("viking://")
    assert source_file.read_text() == before_content
    assert source_file.stat().st_mtime_ns == before_mtime


async def test_add_resource_accepts_local_directory_path_when_enabled(
    client: httpx.AsyncClient,
    temp_dir,
    upload_temp_dir,
    monkeypatch,
):
    _patch_resources_config(monkeypatch, upload_temp_dir, allow_local_path=True)
    source_dir = temp_dir / "source_dir"
    source_dir.mkdir()
    (source_dir / "a.md").write_text("# a\n")
    nested_dir = source_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "b.txt").write_text("hello\n")
    before_snapshot = _snapshot_directory(source_dir)

    resp = await client.post(
        "/api/v1/resources",
        json={"path": str(source_dir), "reason": "local directory path"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"].startswith("viking://")
    assert _snapshot_directory(source_dir) == before_snapshot


async def test_add_resource_accepts_temp_uploaded_file(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    upload_resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("sample.md", b"# upload\n", "text/markdown")},
    )
    temp_file_id = upload_resp.json()["result"]["temp_file_id"]

    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": temp_file_id, "reason": "uploaded resource"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"].startswith("viking://")


async def test_add_resource_rejects_temp_file_id_directory(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    temp_subdir = upload_temp_dir / "dir_upload"
    temp_subdir.mkdir()

    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": temp_subdir.name, "reason": "dir upload"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_rejects_temp_file_id_symlink(
    client: httpx.AsyncClient,
    upload_temp_dir,
    tmp_path,
):
    real_file = tmp_path / "outside.md"
    real_file.write_text("# outside\n")
    symlink_path = upload_temp_dir / "linked.md"
    symlink_path.symlink_to(real_file)

    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": symlink_path.name, "reason": "symlink upload"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"
