# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Exercise Skill update failures through the HTTP API with local fake models."""

import asyncio
import threading
import zipfile

import pytest

from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_index import get_task_context
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint


async def _upload_package(client, tmp_path, name, description, files):
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("SKILL.md", _skill_md(name, description))
        for path, content in files.items():
            package.writestr(path, content)
    with archive.open("rb") as handle:
        response = await client.post(
            "/api/v1/resources/temp_upload",
            files={"file": (archive.name, handle, "application/zip")},
        )
    assert response.status_code == 200, response.text
    return response.json()["result"]["temp_file_id"]


async def _wait_until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def _download(client, uri):
    response = await client.get("/api/v1/content/download", params={"uri": uri})
    assert response.status_code == 200, response.text
    return response.content


async def _indexed_record(client, uri, level):
    response = await client.post(
        "/api/v1/search/find",
        json={
            "query": "",
            "target_uri": uri,
            "level": [level],
            "filter": {"op": "must", "field": "uri", "conds": [uri], "para": "-d=0"},
        },
    )
    assert response.status_code == 200, response.text
    return [
        (hit["uri"], hit["level"], hit["abstract"]) for hit in response.json()["result"]["skills"]
    ]


@pytest.mark.parametrize("cancel_mode", ["timeout", "task"])
async def test_update_cancellation_restores_package_and_index(
    client, tmp_path, monkeypatch, cancel_mode
):
    from openviking.storage.queuefs import semantic_dag

    name = "timeout-package-rollback"
    old_upload = await _upload_package(
        client,
        tmp_path,
        name,
        "Original description",
        {"references/old.md": "Original recovery instructions."},
    )
    added = await client.post("/api/v1/skills", json={"temp_file_id": old_upload, "wait": True})
    assert added.status_code == 200, added.text
    root = added.json()["result"]["root_uri"]
    paths = (
        "SKILL.md",
        ".abstract.md",
        ".overview.md",
        "references/old.md",
        "references/.abstract.md",
        "references/.overview.md",
    )
    old_contents = {path: await _download(client, f"{root}/{path}") for path in paths}
    record_locations = (
        (root, 0),
        (root, 1),
        (f"{root}/SKILL.md", 2),
        (f"{root}/references", 0),
        (f"{root}/references", 1),
        (f"{root}/references/old.md", 2),
    )
    old_records = {
        location: await _indexed_record(client, *location) for location in record_locations
    }
    assert all(len(records) == 1 for records in old_records.values())

    files = {"references/000-fast.md": "A replacement file that reaches the index."}
    files.update({f"references/slow-{index}.md": "Unfinished replacement." for index in range(8)})
    new_upload = await _upload_package(client, tmp_path, name, "Replacement description", files)
    release = threading.Event()
    fast_finished = threading.Event()
    started = set()
    cancelled = set()
    finished = set()
    task_owners = {}
    original_summary = SemanticProcessor._generate_single_file_summary
    original_scheduler = semantic_dag.get_semantic_node_scheduler

    async def hold_summary(self, file_path, *args, **kwargs):
        task_context = get_task_context()
        task_owners[task_context.task_id] = task_context
        if file_path.endswith("/000-fast.md"):
            result = await original_summary(self, file_path, *args, **kwargs)
            fast_finished.set()
            return result
        started.add(file_path)
        try:
            while not release.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            cancelled.add(file_path)
            raise
        finished.add(file_path)
        return await original_summary(self, file_path, *args, **kwargs)

    monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", hold_summary)
    monkeypatch.setattr(
        semantic_dag, "get_semantic_node_scheduler", lambda _workers: original_scheduler(2)
    )
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={
                "temp_file_id": new_upload,
                "wait": True,
                "timeout": 2 if cancel_mode == "timeout" else 10,
            },
        )
    )
    try:
        await _wait_until(lambda: fast_finished.is_set() and len(started) == 2)
        async with asyncio.timeout(1):
            while not await _indexed_record(client, f"{root}/references/000-fast.md", 2):
                await asyncio.sleep(0.01)
        if cancel_mode == "task":
            assert len(task_owners) == 1
            owner = next(iter(task_owners.values()))
            await get_task_tracker().cancel(
                owner.task_id, account_id=owner.account_id, user_id=owner.user_id
            )
        response = await asyncio.wait_for(asyncio.shield(updating), timeout=5)
        expected_error = "DEADLINE_EXCEEDED" if cancel_mode == "timeout" else "PROCESSING_ERROR"
        assert response.status_code >= 400, response.text
        assert response.json()["error"]["code"] == expected_error
        assert cancelled == started
        assert not finished, "Rollback must not wait for blocked summaries to finish normally"
        assert len(started) < len(files), "Pending summaries should be skipped on cancellation"

        # Releasing the fault after the response must not let stale work modify the restored pack.
        release.set()
        await get_queue_manager().wait_complete(timeout=5)
        for path, content in old_contents.items():
            assert await _download(client, f"{root}/{path}") == content
        for location, records in old_records.items():
            assert await _indexed_record(client, *location) == records
        for path in files:
            missing = await client.get("/api/v1/content/download", params={"uri": f"{root}/{path}"})
            assert missing.status_code == 404, missing.text
            assert await _indexed_record(client, f"{root}/{path}", 2) == []
    finally:
        release.set()
        await get_queue_manager().wait_complete(timeout=5)
        if not updating.done():
            updating.cancel()
        await asyncio.gather(updating, return_exceptions=True)


async def test_async_update_background_failure_keeps_successfully_returned_replacement(
    client, monkeypatch
):
    name = "async-failure-keeps-replacement"
    await _add_skill(client, name, "Original description")
    release_failure = threading.Event()

    async def fail_after_response(self, file_path, *args, **kwargs):
        while not release_failure.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError("injected background summary failure")

    monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", fail_after_response)
    try:
        response = await client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Replacement description"), "wait": False},
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["result"]["task_id"]
        release_failure.set()
        async with asyncio.timeout(5):
            while True:
                task_response = await client.get(f"/api/v1/tasks/{task_id}")
                assert task_response.status_code == 200, task_response.text
                status = task_response.json()["result"]["status"]
                if status in {"failed", "cancelled", "completed"}:
                    break
                await asyncio.sleep(0.01)
        assert status == "failed", task_response.text
        shown = await client.get(f"/api/v1/skills/{name}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"]["description"] == "Replacement description"
    finally:
        release_failure.set()
        await get_queue_manager().wait_complete(timeout=5)
