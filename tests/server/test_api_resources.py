# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for resource management endpoints."""

import asyncio
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.resources import AddResourceRequest
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_add_resource_request_defaults_processing_mode():
    request = AddResourceRequest(path="https://example.com/demo.md")

    assert request.processing_mode == "semantic_and_vectors"
    assert request.is_active is True


def test_add_resource_request_accepts_declared_add_type():
    request = AddResourceRequest(
        path="https://example.com/space",
        add_type=" feishu ",
        to="viking://resources/feishu",
    )

    assert request.add_type == "feishu"
    assert request.to == "viking://resources/feishu"


@pytest.mark.parametrize(
    "target",
    [
        {"to": "viking://resources/docs"},
        {"parent": "viking://resources"},
    ],
)
def test_add_resource_request_accepts_paused_watch_with_target(target):
    request = AddResourceRequest(
        path="https://example.feishu.cn/docx/doc_token",
        watch_interval=30,
        is_active=False,
        **target,
    )

    assert request.is_active is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"watch_interval": 0, "to": "viking://resources/docs"},
        {"watch_interval": 30},
    ],
)
def test_add_resource_request_rejects_invalid_paused_watch(kwargs):
    with pytest.raises(ValueError, match="is_active=false"):
        AddResourceRequest(
            path="tos://bucket/docs/",
            is_active=False,
            **kwargs,
        )


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


async def _wait_task_terminal(client: httpx.AsyncClient, task_id: str, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    last_result = None
    while asyncio.get_running_loop().time() < deadline:
        task_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert task_resp.status_code == 200
        last_result = task_resp.json()["result"]
        if last_result["status"] in {"completed", "failed", "cancelled"}:
            return last_result
        await asyncio.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not finish; last_result={last_result}")


async def test_add_resource_with_wait(
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    expected_content = sample_markdown_file.read_text().strip()
    payload = {
        "temp_file_id": sample_markdown_file.name,
        "reason": "test resource",
        "wait": True,
    }
    resp = await client.post("/api/v1/resources", json={**payload, "timeout": 0.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "task_id" not in result
    assert "time" not in body
    assert "usage" not in body
    assert "telemetry" not in body
    response = await client.get(
        "/api/v1/content/read", params={"uri": f"{result['root_uri']}/{sample_markdown_file.name}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"].strip() == expected_content


@pytest.mark.usefixtures("public_resource_dns")
async def test_add_resource_remote_empty_with_wait_returns_invalid_argument(
    client: httpx.AsyncClient,
    temp_dir,
    monkeypatch,
):
    from openviking.parse.accessors.base import LocalResource, SourceType
    from openviking.parse.accessors.http_accessor import HTTPAccessor

    downloaded = temp_dir / "remote-empty.txt"
    downloaded.write_bytes(b"")

    async def return_empty_download(self, source, **kwargs):
        return LocalResource(
            path=downloaded,
            source_type=SourceType.HTTP,
            original_source=str(source),
            meta={"original_filename": downloaded.name},
            is_temporary=True,
        )

    monkeypatch.setattr(HTTPAccessor, "access", return_empty_download)

    resp = await client.post(
        "/api/v1/resources",
        json={"path": "https://example.com/remote-empty.txt", "wait": True},
    )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "empty" in body["error"]["message"].lower()
    assert not downloaded.exists()


async def test_add_resource_internal_task_can_be_polled_but_is_hidden_from_default_list(
    client: httpx.AsyncClient, sample_markdown_file, upload_temp_dir
):
    response = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": sample_markdown_file.name, "internal_task": True, "wait": False},
    )
    assert response.status_code == 200, response.text
    submitted = response.json()["result"]
    task = await _wait_task_terminal(client, submitted["task_id"])
    assert task["status"] == "completed", task
    assert task["result"]["root_uri"] == submitted["root_uri"]

    filters = {"task_type": "add_resource", "resource_id": submitted["root_uri"]}
    response = await client.get("/api/v1/tasks", params=filters)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == []
    response = await client.get("/api/v1/tasks", params={**filters, "include_internal": True})
    assert response.status_code == 200, response.text
    assert [item["task_id"] for item in response.json()["result"]] == [submitted["task_id"]]


async def test_add_resource_vectors_only_indexes_file_without_semantic_sidecars(
    client: httpx.AsyncClient, sample_markdown_file, upload_temp_dir, monkeypatch
):
    completion = AsyncMock()
    monkeypatch.setattr(VLMConfig, "get_completion_async", completion)
    response = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "processing_mode": "vectors_only",
            "wait": True,
        },
    )
    assert response.status_code == 200, response.text
    root = response.json()["result"]["root_uri"]
    file_uri = f"{root}/{sample_markdown_file.name}"
    for sidecar in (".abstract.md", ".overview.md"):
        stat = await client.get("/api/v1/fs/stat", params={"uri": f"{root}/{sidecar}"})
        assert stat.status_code == 404, stat.text
    found = await client.post(
        "/api/v1/search/find",
        json={
            "query": "",
            "target_uri": root,
            "level": [2],
            "filter": {"op": "must", "field": "uri", "conds": [file_uri]},
        },
    )
    assert found.status_code == 200, found.text
    assert [item["uri"] for item in found.json()["result"]["resources"]] == [file_uri]
    completion.assert_not_awaited()


async def test_add_resource_refresh_applies_tag_intent(client: httpx.AsyncClient, upload_temp_dir):
    target = "viking://resources/tagged-import"
    file_uri = f"{target}/plan.md"
    for mode, tags, content, expected in (
        ("replace", ["team=search"], "# Initial plan", {"team=search"}),
        ("append", ["env=prod"], "# Updated plan", {"team=search", "env=prod"}),
        ("replace", [], "# Updated plan again", {"team=search", "env=prod"}),
        ("clear", ["unused=tag"], "# Final plan", set()),
    ):
        source = upload_temp_dir / "plan.md"
        source.write_text(content)
        imported = await client.post(
            "/api/v1/resources",
            json={
                "temp_file_id": source.name,
                "to": target,
                "tags": tags,
                "tag_mode": mode,
                "wait": True,
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["result"]["root_uri"] == target
        entries = await client.get("/api/v1/fs/ls", params={"uri": target, "include_tags": True})
        assert entries.status_code == 200, entries.text
        entry = next(item for item in entries.json()["result"] if item["uri"] == file_uri)
        assert set(entry.get("tags", [])) == expected


@pytest.mark.usefixtures("public_resource_dns")
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


@pytest.mark.usefixtures("public_resource_dns")
async def test_add_resource_business_error_uses_error_envelope(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    upload = upload_temp_dir / "broken.png"
    # PNG with an invalid IDAT checksum.
    upload.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
            "0000000c49444154789c636060f80f00010301000889c2ed0000000049454e44ae426082"
        )
    )

    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": upload.name, "wait": True},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"description": "Skill without name", "content": "# No Name"},
            "Skill must have 'name' field",
        ),
        ("---\nname: [broken\ndescription: invalid YAML\n---\n# Skill", "Invalid YAML frontmatter"),
    ],
)
async def test_add_skill_invalid_content_returns_invalid_argument(
    client: httpx.AsyncClient, data, message
):
    resp = await client.post("/api/v1/skills", json={"data": data})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "result" not in body or body["result"] is None
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert message in body["error"]["message"]


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
    client: httpx.AsyncClient, sample_markdown_file, upload_temp_dir
):
    target = "viking://resources/custom/sample"
    content = sample_markdown_file.read_text()
    response = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": sample_markdown_file.name, "to": target, "wait": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["root_uri"] == target
    response = await client.get(
        "/api/v1/content/read", params={"uri": f"{target}/{sample_markdown_file.name}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"].strip() == content.strip()


@pytest.mark.parametrize("archive_state", ["valid", "invalid", "bad_crc", "io_error"])
async def test_add_resource_with_resources_root_to_uses_child_uri(
    client: httpx.AsyncClient,
    upload_temp_dir,
    monkeypatch,
    archive_state,
):
    archive_path = upload_temp_dir / "tt_b.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    if archive_state == "invalid":
        archive_path.write_bytes(b"not a zip archive")
    elif archive_state == "bad_crc":
        archive_path.write_bytes(archive_path.read_bytes().replace(b"# hello", b"# wrong"))
    elif archive_state == "io_error":

        def fail_to_open(*args, **kwargs):
            raise OSError("disk unavailable")

        monkeypatch.setattr(zipfile, "ZipFile", fail_to_open)

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": archive_path.name,
            "to": "viking://resources/",
            "reason": "test resource root import",
            "wait": True,
        },
    )
    if archive_state != "valid":
        is_bad_input = archive_state != "io_error"
        assert resp.status_code == (400 if is_bad_input else 500), resp.text
        error = resp.json()["error"]
        assert error["code"] == ("INVALID_ARGUMENT" if is_bad_input else "PROCESSING_ERROR")
        assert ("Not a valid ZIP file" if is_bad_input else "disk unavailable") in error["message"]
        return

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["root_uri"] == "viking://resources/tt_b"
    root = body["result"]["root_uri"]
    files = await client.get(
        "/api/v1/fs/ls", params={"uri": root, "simple": True, "recursive": True}
    )
    target = f"{root}/bb/readme/readme.md"
    assert files.status_code == 200, files.text
    assert target in files.json()["result"], files.text
    read = await client.get("/api/v1/content/read", params={"uri": target})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == "# hello"


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


async def test_add_resource_non_wait_same_name_preserves_both_files(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    files = [
        ("first.md", "# first", "viking://resources/same"),
        ("second.md", "# second", "viking://resources/same_1"),
    ]
    submitted = []
    for filename, content, root_uri in files:
        (upload_temp_dir / filename).write_text(f"{content}\n", encoding="utf-8")
        response = await client.post(
            "/api/v1/resources",
            json={
                "temp_file_id": filename,
                "source_name": "same.md",
                "wait": False,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok"
        result = body["result"]
        assert result["status"] == "success"
        assert "source_path" in result
        assert result["root_uri"] == root_uri
        submitted.append((result["task_id"], root_uri, content))

    assert submitted[0][0] != submitted[1][0]
    for task_id, root_uri, content in submitted:
        task = await _wait_task_terminal(client, task_id)
        assert task["task_id"] == task_id
        assert task["task_type"] == "add_resource"
        assert task["status"] == "completed", task
        assert task["result"]["root_uri"] == root_uri

        response = await client.get("/api/v1/content/read", params={"uri": f"{root_uri}/same.md"})
        assert response.status_code == 200, response.text
        assert response.json()["result"].strip() == content


async def test_wait_processed_empty_queue(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/system/wait",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_temp_upload_uses_configured_shared_default(client, app):
    app.state.config.temp_upload.default_mode = "shared"
    content = "# Upload with the configured storage mode"
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("configured.md", content, "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    temp_file_id = uploaded.json()["result"]["temp_file_id"]
    assert temp_file_id.startswith("shared_")

    imported = await client.post(
        "/api/v1/resources", json={"temp_file_id": temp_file_id, "wait": True}
    )
    assert imported.status_code == 200, imported.text
    root = imported.json()["result"]["root_uri"]
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/configured.md"})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content


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


async def test_add_resource_accepts_temp_uploaded_file(client: httpx.AsyncClient, upload_temp_dir):
    content = "# Uploaded resource"
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("sample.md", content, "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["status"] == "ok"
    assert "telemetry" not in body
    temp_file_id = body["result"]["temp_file_id"]
    assert temp_file_id.endswith(".md")
    assert "/" not in temp_file_id

    imported = await client.post(
        "/api/v1/resources", json={"temp_file_id": temp_file_id, "wait": True}
    )
    assert imported.status_code == 200, imported.text
    root = imported.json()["result"]["root_uri"]
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/sample.md"})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content


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

    for expected_root in ("viking://resources/shared", "viking://resources/shared_1"):
        resp = await client.post(
            "/api/v1/resources",
            json={"temp_file_id": temp_file_id, "wait": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        root = body["result"]["root_uri"]
        assert root == expected_root
        read = await client.get("/api/v1/content/read", params={"uri": f"{root}/shared.md"})
        assert read.status_code == 200, read.text
        assert read.json()["result"].strip() == "# shared upload"


async def test_add_resource_creates_paused_git_watch_and_rejects_shared_upload_watch(
    client: httpx.AsyncClient, service, upload_temp_dir, tmp_path, monkeypatch
):
    from openviking.parse.accessors.base import LocalResource, SourceType
    from openviking.parse.accessors.git_accessor import GitAccessor

    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    (repository_path / "README.md").write_text("# Watched repository\n")
    source = "https://github.com/team/watched.git"
    target = "viking://resources/watched-repository"
    revision = "a" * 40
    monkeypatch.setattr(
        "openviking.utils.network_guard._resolve_host_addresses", lambda _host: {"140.82.121.4"}
    )
    process = AsyncMock(returncode=0)
    process.communicate.return_value = (f"{revision}\tHEAD\n".encode(), b"")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(
        GitAccessor,
        "access",
        AsyncMock(
            return_value=LocalResource(
                path=repository_path,
                source_type=SourceType.GIT,
                original_source=source,
                meta={"repo_name": "team/watched", "resolved_head_sha": revision},
                is_temporary=False,
            )
        ),
    )
    response = await client.post(
        "/api/v1/resources",
        json={
            "path": source,
            "to": target,
            "watch_interval": 5,
            "is_active": False,
            "wait": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["root_uri"] == target
    response = await client.get("/api/v1/watches", params={"to_uri": target})
    assert response.status_code == 200, response.text
    watch = response.json()["result"]
    assert watch["is_active"] is False
    assert watch["watch_interval"] == 5
    assert watch["next_execution_time"] is None

    upload_resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("watched.md", b"# watched upload\n", "text/markdown")},
        data={"upload_mode": "shared"},
    )
    assert upload_resp.status_code == 200
    temp_file_id = upload_resp.json()["result"]["temp_file_id"]
    target_uri = "viking://resources/watched-shared-upload.md"

    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": temp_file_id,
            "to": target_uri,
            "wait": True,
            "watch_interval": 5.0,
            "tags": ["team=watch", "env=test"],
            "tag_mode": "replace",
        },
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert "uploaded content" in resp.json()["error"]["message"]
    task = await service.watch_manager.get_task_by_uri(
        to_uri=target_uri,
        account_id="default",
        user_id="test_user",
        role="ROOT",
    )
    assert task is None
    assert not (upload_temp_dir / "watch_sources").exists()


async def test_shared_temp_upload_failed_consume_is_retryable(
    client: httpx.AsyncClient, service, monkeypatch
):
    content = "# Retriable shared upload"
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("retry.md", content, "text/markdown")},
        data={"upload_mode": "shared"},
    )
    assert uploaded.status_code == 200, uploaded.text
    temp_file_id = uploaded.json()["result"]["temp_file_id"]
    payload = {"temp_file_id": temp_file_id, "to": "viking://resources/retry"}
    read_bytes = service.viking_fs.read_file_bytes

    async def fail_shared_read(uri, **kwargs):
        if uri.startswith("viking://upload/"):
            raise OSError("shared content temporarily unavailable")
        return await read_bytes(uri, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(service.viking_fs, "read_file_bytes", fail_shared_read)
        submitted = await client.post("/api/v1/resources", json={**payload, "wait": False})
        assert submitted.status_code == 200, submitted.text
        task = await _wait_task_terminal(client, submitted.json()["result"]["task_id"])
        assert task["status"] == "failed", task
        assert "shared content temporarily unavailable" in task["error"]

    retry = await client.post("/api/v1/resources", json={**payload, "wait": True})
    assert retry.status_code == 200, retry.text
    root = retry.json()["result"]["root_uri"]
    assert root == payload["to"]
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/retry.md"})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content


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
