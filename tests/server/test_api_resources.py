# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for resource management endpoints."""

import zipfile
from pathlib import Path

import httpx

from openviking.telemetry import get_current_telemetry


def _write_zip(zip_path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in files.items():
            zf.writestr(arcname, content)


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


async def test_add_resource_file_to_trailing_slash_rule_1(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "to": "viking://resources/custom_dir/",
            "reason": "rule 1",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    root_uri = body["result"]["root_uri"]
    assert root_uri == "viking://resources/custom_dir/sample.md"

    stat_resp = await client.get("/api/v1/fs/stat", params={"uri": root_uri})
    assert stat_resp.status_code == 200
    assert stat_resp.json()["result"]["isDir"] is False


async def test_add_resource_file_to_no_trailing_slash_rule_2(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    target = "viking://resources/custom_dir/renamed.md"
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "to": target,
            "reason": "rule 2",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    root_uri = body["result"]["root_uri"]
    assert root_uri == target

    stat_resp = await client.get("/api/v1/fs/stat", params={"uri": root_uri})
    assert stat_resp.status_code == 200
    assert stat_resp.json()["result"]["isDir"] is False


async def test_add_resource_dir_to_trailing_slash_rule_3(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    zip_path = upload_temp_dir / "demo_dir.zip"
    _write_zip(
        zip_path,
        {
            "demo_dir/a.md": "# a\n",
            "demo_dir/b.md": "# b\n",
        },
    )
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": zip_path.name,
            "to": "viking://resources/custom_dir/",
            "reason": "rule 3",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    root_uri = body["result"]["root_uri"]
    assert root_uri == "viking://resources/custom_dir/demo_dir/"

    stat_resp = await client.get("/api/v1/fs/stat", params={"uri": root_uri})
    assert stat_resp.status_code == 200
    assert stat_resp.json()["result"]["isDir"] is True


async def test_add_resource_dir_to_no_trailing_slash_rule_4(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    zip_path = upload_temp_dir / "demo_dir.zip"
    _write_zip(
        zip_path,
        {
            "demo_dir/a.md": "# a\n",
            "demo_dir/b.md": "# b\n",
        },
    )
    target = "viking://resources/custom_dir/explicit_root"
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": zip_path.name,
            "to": target,
            "reason": "rule 4",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    root_uri = body["result"]["root_uri"]
    assert root_uri == target

    stat_resp = await client.get("/api/v1/fs/stat", params={"uri": root_uri})
    assert stat_resp.status_code == 200
    assert stat_resp.json()["result"]["isDir"] is True


async def test_add_resource_zip_single_file_follows_rules_rule_5(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    zip_path = upload_temp_dir / "single_file.zip"
    _write_zip(zip_path, {"hello.md": "# hello\n"})
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": zip_path.name,
            "to": "viking://resources/custom_dir/",
            "reason": "rule 5",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    root_uri = body["result"]["root_uri"]
    assert root_uri == "viking://resources/custom_dir/hello.md"

    stat_resp = await client.get("/api/v1/fs/stat", params={"uri": root_uri})
    assert stat_resp.status_code == 200
    assert stat_resp.json()["result"]["isDir"] is False


async def test_add_resource_to_resources_root_is_rejected_rule_6(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "to": "viking://resources",
            "reason": "rule 6",
            "wait": True,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "viking://resources" in body["error"]["message"]
    assert "viking://resources/" in body["error"]["message"]


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


async def test_add_resource_rejects_direct_local_path(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "/app/ov.conf", "reason": "security test"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


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
