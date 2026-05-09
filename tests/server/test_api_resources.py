# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for resource management endpoints."""

import asyncio
import zipfile

import httpx

from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry


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


async def test_add_skill_missing_name_returns_invalid_argument(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={
            "data": {
                "description": "Skill without name",
                "content": "# No Name Skill\nTest content.",
            },
            "wait": True,
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "result" not in body or body["result"] is None
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert body["error"]["message"] == "Skill must have 'name' field"


async def test_add_skill_empty_dict_returns_invalid_argument(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={"data": {}, "wait": True},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "name" in body["error"]["message"]


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


async def test_shared_temp_upload_and_add_resource_deletes_upload_dir(
    client: httpx.AsyncClient,
    service,
):
    upload_resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("shared.md", b"# shared upload\n", "text/markdown")},
        data={"upload_mode": "shared"},
    )
    assert upload_resp.status_code == 200
    temp_file_id = upload_resp.json()["result"]["temp_file_id"]
    assert temp_file_id.startswith("shared_")

    upload_id = temp_file_id[len("shared_") :]
    upload_root = f"viking://upload/{upload_id}"
    vfs = get_viking_fs()
    assert await vfs.exists(f"{upload_root}/meta.json")
    assert await vfs.exists(f"{upload_root}/content")

    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": temp_file_id, "reason": "shared upload"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"].startswith("viking://")
    assert not await vfs.exists(upload_root)


async def test_shared_temp_upload_failed_consume_is_retryable(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    upload_resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("shared.md", b"# shared upload\n", "text/markdown")},
        data={"upload_mode": "shared"},
    )
    temp_file_id = upload_resp.json()["result"]["temp_file_id"]

    async def fake_add_resource(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)
    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": temp_file_id, "reason": "shared upload"},
    )
    assert resp.status_code == 500

    upload_id = temp_file_id[len("shared_") :]
    meta_uri = f"viking://upload/{upload_id}/meta.json"
    meta_raw = await get_viking_fs().read_file(meta_uri)
    assert '"state": "uploaded"' in meta_raw


async def test_shared_upload_fs_read_is_denied_for_non_root(
    client: httpx.AsyncClient,
):
    upload_resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("shared.md", b"# shared upload\n", "text/markdown")},
        data={"upload_mode": "shared"},
    )
    assert upload_resp.status_code == 200
    temp_file_id = upload_resp.json()["result"]["temp_file_id"]
    upload_id = temp_file_id[len("shared_") :]

    resp = await client.get(
        "/api/v1/fs/read",
        params={"uri": f"viking://upload/{upload_id}/meta.json"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


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


async def test_add_resource_async_returns_task_id(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test async task tracking",
            "wait": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "task_id" in body["result"]
    assert body["result"]["task_id"]


async def test_add_resource_sync_no_task_id(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test sync no task_id",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "task_id" not in body["result"]


async def test_add_resource_async_task_queryable(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    from openviking.service.task_tracker import reset_task_tracker

    reset_task_tracker()

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "test task queryable",
            "wait": False,
        },
    )
    task_id = resp.json()["result"]["task_id"]

    await asyncio.sleep(2.0)

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    result = task_resp.json()["result"]
    assert result["task_id"] == task_id
    assert result["task_type"] == "add_resource"
    assert result["status"] in {"running", "completed", "failed"}


async def test_add_resource_async_failure_cleans_up_tracker(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
    upload_temp_dir,
):
    """Regression: when wait=False and telemetry_id is registered but processor
    raises before task/monitor creation, RequestWaitTracker and telemetry
    registry must not leak state."""

    from openviking.server.identity import RequestContext, Role
    from openviking.telemetry.registry import _REGISTERED_TELEMETRY
    from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
    from openviking_cli.session.user_id import UserIdentifier

    async def _failing_process_resource(**kwargs):
        raise RuntimeError("processor exploded")

    monkeypatch.setattr(
        service.resources._resource_processor, "process_resource", _failing_process_resource
    )

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    rwt_before = set(get_request_wait_tracker()._states.keys())
    tel_before = set(_REGISTERED_TELEMETRY.keys())

    try:
        await service.resources.add_resource(
            path="/tmp/fail_test.md",
            ctx=ctx,
            reason="failure cleanup test",
            wait=False,
        )
    except RuntimeError:
        pass

    rwt_after = set(get_request_wait_tracker()._states.keys())
    tel_after = set(_REGISTERED_TELEMETRY.keys())

    leaked_rwt = rwt_after - rwt_before
    assert not leaked_rwt, f"RequestWaitTracker leaked: {leaked_rwt}"

    leaked_telemetry = tel_after - tel_before
    assert not leaked_telemetry, f"Telemetry registry leaked: {leaked_telemetry}"


async def test_add_resource_business_error_no_task(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
    upload_temp_dir,
):
    """When process_resource returns status=error, no task should be created."""

    from openviking.service.task_tracker import get_task_tracker

    async def _error_process_resource(**kwargs):
        return {"status": "error", "message": "unsupported format"}

    monkeypatch.setattr(
        service.resources._resource_processor, "process_resource", _error_process_resource
    )

    task_count_before = get_task_tracker().count()

    await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": "nonexistent",
            "reason": "test business error",
            "wait": False,
        },
    )

    task_count_after = get_task_tracker().count()
    assert task_count_after == task_count_before, "Business error should not create a task"


async def test_monitor_marks_failed_on_queue_error(
    service,
    monkeypatch,
):
    """When queue processing has errors, _monitor_queue_processing should mark task as failed."""

    from openviking.server.identity import RequestContext, Role
    from openviking.service.task_tracker import get_task_tracker, reset_task_tracker
    from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
    from openviking_cli.session.user_id import UserIdentifier

    reset_task_tracker()

    async def _fake_process_resource(**kwargs):
        return {"status": "success", "root_uri": "viking://resources/queue-err-test"}

    monkeypatch.setattr(
        service.resources._resource_processor, "process_resource", _fake_process_resource
    )

    original_wait_for_request = get_request_wait_tracker().wait_for_request

    async def _mock_wait_then_error(telemetry_id, timeout=None, poll_interval=0.05):
        rwt = get_request_wait_tracker()
        with rwt._lock:
            state = rwt._states.get(telemetry_id)
            if state:
                state.semantic_error_count = 1
                state.semantic_errors.append("semantic processing failed")
                state.pending_semantic_roots.clear()
                state.pending_embedding_roots.clear()
        await original_wait_for_request(telemetry_id, timeout=timeout, poll_interval=0.01)

    monkeypatch.setattr(get_request_wait_tracker(), "wait_for_request", _mock_wait_then_error)

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_resource(
        path="/tmp/queue_err_test.md",
        ctx=ctx,
        reason="queue error test",
        wait=False,
    )

    task_id = result.get("task_id")
    assert task_id, "Expected task_id in result"

    await asyncio.sleep(1.0)

    task = get_task_tracker().get(task_id)
    assert task is not None
    assert task.status.value == "failed", f"Expected failed, got {task.status.value}"
    assert "queue processing failed" in task.error
