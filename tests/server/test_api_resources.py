# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for resource management endpoints."""

import asyncio
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources as resources_router
from openviking.server.routers.resources import AddResourceRequest
from openviking.telemetry import get_current_telemetry
from openviking_cli.session.user_id import UserIdentifier


def test_add_resource_request_accepts_processing_mode():
    request = AddResourceRequest(
        path="https://example.com/demo.md",
        processing_mode="vectors_only",
    )

    assert request.processing_mode == "vectors_only"


def test_add_resource_request_defaults_processing_mode():
    request = AddResourceRequest(path="https://example.com/demo.md")

    assert request.processing_mode == "semantic_and_vectors"


def test_add_resource_request_accepts_declared_add_type():
    request = AddResourceRequest(
        path="https://example.com/space",
        add_type=" feishu ",
        to="viking://resources/feishu",
    )

    assert request.add_type == "feishu"
    assert request.to == "viking://resources/feishu"


def test_add_resource_request_rejects_add_type_with_temp_file_id():
    import pytest

    with pytest.raises(ValueError, match="temp_file_id"):
        AddResourceRequest(temp_file_id="upload_abc", add_type="feishu")


def test_add_resource_request_requires_exact_to_for_declared_add_type():
    import pytest

    with pytest.raises(ValueError, match="exact 'to'"):
        AddResourceRequest(path="space:home", add_type="feishu")


def test_add_resource_request_rejects_add_type_with_parent():
    import pytest

    with pytest.raises(ValueError, match="'parent'"):
        AddResourceRequest(
            path="space:home",
            add_type="feishu",
            to="viking://resources/feishu",
            parent="viking://resources/imports",
        )


def test_require_remote_resource_source_allows_declared_add_type():
    from openviking.server.local_input_guard import require_remote_resource_source

    assert (
        require_remote_resource_source("space:home", declared_connector_add_type="feishu")
        == "space:home"
    )


def test_require_remote_resource_source_still_rejects_without_declared_type():
    import pytest

    from openviking.server.local_input_guard import require_remote_resource_source
    from openviking_cli.exceptions import PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        require_remote_resource_source("/etc/passwd")


async def _wait_task_terminal(client: httpx.AsyncClient, task_id: str, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    last_result = None
    while asyncio.get_running_loop().time() < deadline:
        task_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert task_resp.status_code == 200
        last_result = task_resp.json()["result"]
        if last_result["status"] in {"completed", "failed"}:
            return last_result
        await asyncio.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not finish; last_result={last_result}")


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
    assert body["result"]["status"] == "success"
    assert body["result"]["root_uri"].startswith("viking://")
    assert "source_path" in body["result"]
    assert body["result"]["task_id"]


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


async def test_add_resource_forwards_args_to_service(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    seen = {}

    async def fake_add_resource(**kwargs):
        seen.update(kwargs)
        return {
            "status": "success",
            "root_uri": "viking://resources/demo",
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": "https://example.com/demo.md",
            "args": {"feishu_access_token": "u-test"},
        },
    )

    assert resp.status_code == 200
    assert seen["args"] == {"feishu_access_token": "u-test"}


async def test_add_resource_forwards_processing_mode_to_service(monkeypatch):
    seen = {}

    async def fake_add_resource(**kwargs):
        seen.update(kwargs)
        return {
            "status": "success",
            "root_uri": "viking://resources/demo",
        }

    service = SimpleNamespace(resources=SimpleNamespace(add_resource=fake_add_resource))
    monkeypatch.setattr(resources_router, "get_service", lambda: service)

    response = await resources_router.add_resource(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=None))),
        AddResourceRequest(
            path="https://example.com/demo.md",
            processing_mode="vectors_only",
        ),
        RequestContext(
            user=UserIdentifier("account-1", "user-1"),
            role=Role.USER,
        ),
    )

    assert response["status"] == "ok"
    assert seen["processing_mode"] == "vectors_only"


async def test_add_resource_forwards_tags_to_service(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    seen = {}

    async def fake_add_resource(**kwargs):
        seen.update(kwargs)
        return {
            "status": "success",
            "root_uri": "viking://resources/demo",
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": "https://example.com/demo.md",
            "tags": ["team=search"],
            "tag_mode": "append",
        },
    )

    assert resp.status_code == 200
    assert seen["tags"] == ["team=search"]
    assert seen["tag_mode"] == "append"


async def test_add_resource_preserves_create_parent_field_presence(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    calls = []

    async def fake_add_resource(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "root_uri": "viking://resources/demo"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    omitted = await client.post(
        "/api/v1/resources",
        json={"path": "https://example.com/demo.md", "to": "viking://resources/demo.md"},
    )
    explicit_false = await client.post(
        "/api/v1/resources",
        json={
            "path": "https://example.com/demo.md",
            "to": "viking://resources/demo.md",
            "create_parent": False,
        },
    )

    assert omitted.status_code == 200
    assert explicit_false.status_code == 200
    assert "create_parent" not in calls[0]
    assert calls[1]["create_parent"] is False


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
    async def fail_during_ingestion(**kwargs):
        return {
            "status": "error",
            "errors": ["Parse error: boom"],
            "source_path": kwargs["path"],
        }

    monkeypatch.setattr(
        service.resources,
        "_execute_resource_ingestion",
        fail_during_ingestion,
    )

    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": "https://example.com/bad.md",
            "reason": "test resource",
            "wait": True,
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
            "wait": True,
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
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/tt_b"


async def test_add_resource_with_home_alias_resources_parent_initializes_root(
    app,
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    from openviking.server.auth import get_request_context

    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "default"),
        role=Role.USER,
    )
    archive_path = upload_temp_dir / "user_short_docs.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("user_short_docs/readme.md", "# hello\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": archive_path.name,
            "parent": "viking://~/resources",
            "reason": "test home alias resource parent import",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://user/default/resources/user_short_docs"


async def test_add_resource_with_peer_resources_root_to_uses_child_uri(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    archive_path = upload_temp_dir / "peer_docs.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("peer_docs/readme.md", "# hello\n")

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": archive_path.name,
            "to": "viking://user/default/peers/alice/resources",
            "reason": "test peer resource root import",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://user/default/peers/alice/resources/peer_docs"


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
            "wait": True,
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
            "wait": True,
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
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/aa"


async def test_add_resource_non_wait_auto_name_resolves_unique_root_uri(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    first = upload_temp_dir / "first.md"
    second = upload_temp_dir / "second.md"
    first.write_text("# first\n")
    second.write_text("# second\n")

    first_resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": first.name,
            "source_name": "same.md",
            "reason": "first async resource",
            "wait": False,
        },
    )
    second_resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": second.name,
            "source_name": "same.md",
            "reason": "second async resource",
            "wait": False,
        },
    )

    assert first_resp.status_code == 200
    assert second_resp.status_code == 200
    first_result = first_resp.json()["result"]
    second_result = second_resp.json()["result"]
    assert first_result["root_uri"] == "viking://resources/same"
    assert second_result["root_uri"] == "viking://resources/same_1"


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
        json={"temp_file_id": sample_markdown_file.name, "reason": "test", "wait": True},
    )
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 60.0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_add_resource_rejects_temp_upload_with_watch_interval(
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
            "wait": True,
        },
    )
    assert resp.status_code == 400
    assert "uploaded content" in resp.text
    assert not (upload_temp_dir / "watch_sources").exists()


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
            "wait": True,
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


async def test_temp_upload_uses_configured_shared_default(monkeypatch):
    saved_modes = []

    class Store:
        async def save_upload(self, file, upload_mode, ctx):
            saved_modes.append(upload_mode)
            return "shared_123"

    async def run_immediately(*, fn, **kwargs):
        return SimpleNamespace(result=await fn(), telemetry=None)

    config = SimpleNamespace(temp_upload=SimpleNamespace(default_mode="shared"))
    request = SimpleNamespace(
        state=SimpleNamespace(signed_upload=None),
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
    )
    monkeypatch.setattr(resources_router.TempUploadStore, "build", lambda _: Store())
    monkeypatch.setattr(resources_router, "run_operation", run_immediately)

    response = await resources_router.temp_upload(
        request=request,
        file=object(),
        telemetry=False,
        upload_mode=None,
        _ctx=object(),
    )

    assert saved_modes == ["shared"]
    assert response["result"]["temp_file_id"] == "shared_123"


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
        json={"temp_file_id": temp_file_id, "reason": "uploaded resource", "wait": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"].startswith("viking://")


async def test_shared_temp_upload_can_be_added_repeatedly(
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

    for reason in ("first shared upload", "second shared upload"):
        resp = await client.post(
            "/api/v1/resources",
            json={"temp_file_id": temp_file_id, "reason": reason, "wait": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["result"]["root_uri"].startswith("viking://")


@pytest.mark.parametrize("upload_mode", ["local", "shared"])
async def test_temp_upload_with_watch_and_tags_is_rejected_without_watch_source(
    client: httpx.AsyncClient,
    service,
    upload_temp_dir,
    upload_mode,
):
    data = {"upload_mode": upload_mode} if upload_mode == "shared" else None
    upload_resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("watched.md", b"# watched upload\n", "text/markdown")},
        data=data,
    )
    assert upload_resp.status_code == 200
    temp_file_id = upload_resp.json()["result"]["temp_file_id"]
    target_uri = f"viking://resources/watched-{upload_mode}-upload.md"

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": temp_file_id,
            "to": target_uri,
            "reason": f"{upload_mode} upload watch",
            "wait": True,
            "watch_interval": 5.0,
            "tags": ["team=watch", "env=test"],
            "tag_mode": "replace",
        },
    )

    assert resp.status_code == 400, resp.text
    assert "uploaded content" in resp.text
    task = await service.watch_scheduler.watch_manager.get_task_by_uri(
        to_uri=target_uri,
        account_id="default",
        user_id="test_user",
        role="ROOT",
    )
    assert task is None
    assert not (upload_temp_dir / "watch_sources").exists()


async def test_shared_temp_upload_failed_consume_is_retryable(
    client: httpx.AsyncClient,
    app,
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

    original_add_resource = service.resources.add_resource
    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        resp = await http_client.post(
            "/api/v1/resources",
            json={"temp_file_id": temp_file_id, "reason": "shared upload", "wait": True},
        )
    assert resp.status_code == 500

    monkeypatch.setattr(service.resources, "add_resource", original_add_resource)
    retry = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": temp_file_id, "reason": "retry shared upload", "wait": True},
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "ok"


async def test_shared_upload_content_read_rejects_internal_scope(
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
        "/api/v1/content/read",
        params={"uri": f"viking://upload/{upload_id}/meta"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_URI"


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


async def test_add_resource_non_wait_returns_queue_task_id(
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
    assert body["result"]["status"] == "success"
    assert "task_id" in body["result"]
    assert body["result"]["task_id"]
    assert body["result"]["root_uri"].startswith("viking://")


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


async def test_add_resource_non_wait_queue_task_queryable(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    from openviking.service.task_tracker import set_task_tracker

    set_task_tracker(None)

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
