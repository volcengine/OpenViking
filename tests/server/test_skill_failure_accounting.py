# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""A failed Skill package settles one queue item, regardless of file count."""

from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint
from tests.server.test_skill_update_cancellation import (
    _download,
    _indexed_record,
    _upload_package,
)


def _fail_package_summaries(monkeypatch, name):
    original = SemanticProcessor._generate_single_file_summary
    failed_paths = ["SKILL.md", "references/a.md", "references/b.md"]

    async def fail_selected(self, file_path, *args, **kwargs):
        if any(file_path.endswith(f"/skills/{name}/{path}") for path in failed_paths):
            raise ValueError("injected file summary failure")
        return await original(self, file_path, *args, **kwargs)

    monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", fail_selected)
    return failed_paths, original


async def _assert_failed_package_settled(client, failed_paths):
    # Resource follow-up work may include a separate parent refresh. Wait for
    # the whole queue, including that work, before checking its final counts.
    waited = await client.post("/api/v1/system/wait", json={"timeout": 5})
    assert waited.status_code == 200, waited.text
    assert waited.json()["result"]["Semantic"]["error_count"] == 1
    queue_manager = get_queue_manager()
    status = await queue_manager.get_queue(queue_manager.SEMANTIC).get_status()
    assert status.pending == 0
    assert status.in_progress == 0
    assert status.error_count == 1
    assert len(status.errors) == 1
    for path in failed_paths:
        assert path in status.errors[0].message

    return status


async def test_failed_skill_files_do_not_break_global_wait_or_followup_work(
    client, monkeypatch, tmp_path, upload_temp_dir
):
    name = "multiple-file-failures"
    failed_paths, _ = _fail_package_summaries(monkeypatch, name)
    upload = await _upload_package(
        client,
        tmp_path,
        name,
        "Failing package",
        {"references/a.md": "First attachment", "references/b.md": "Second attachment"},
    )
    response = await client.post(
        "/api/v1/skills", json={"temp_file_id": upload, "wait": True, "timeout": 5}
    )
    assert response.status_code == 500, response.text
    # The request still reports every failed file, not merely the first failure.
    for path in failed_paths:
        assert path in response.json()["error"]["message"]
    before = await _assert_failed_package_settled(client, failed_paths)

    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("healthy.md", b"# A healthy resource\nContent.\n", "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    added = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": uploaded.json()["result"]["temp_file_id"], "wait": True},
    )
    assert added.status_code == 200, added.text

    after = await _assert_failed_package_settled(client, failed_paths)
    assert after.processed > before.processed


async def test_update_with_multiple_file_failures_restores_package_and_settles_queue(
    client, monkeypatch, tmp_path, upload_temp_dir
):
    name = "update-multiple-file-failures"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    old_content = await _download(client, f"{root}/SKILL.md")
    old_index = await _indexed_record(client, root, 0)
    failed_paths, original = _fail_package_summaries(monkeypatch, name)
    upload = await _upload_package(
        client,
        tmp_path,
        name,
        "Failed replacement",
        {"references/a.md": "First attachment", "references/b.md": "Second attachment"},
    )

    response = await client.put(
        f"/api/v1/skills/{name}",
        json={"temp_file_id": upload, "wait": True, "timeout": 5},
    )
    assert response.status_code == 500, response.text
    for path in failed_paths:
        assert path in response.json()["error"]["message"]
    before = await _assert_failed_package_settled(client, failed_paths)
    assert await _download(client, f"{root}/SKILL.md") == old_content
    assert await _indexed_record(client, root, 0) == old_index

    monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", original)
    retry = await client.put(
        f"/api/v1/skills/{name}",
        json={"data": _skill_md(name, "Successful replacement"), "wait": True},
    )
    assert retry.status_code == 200, retry.text
    after = await _assert_failed_package_settled(client, failed_paths)
    assert after.processed > before.processed
