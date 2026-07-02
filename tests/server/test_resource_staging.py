# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for staged resource ingestion."""

import asyncio

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _stage_upload(
    client: httpx.AsyncClient,
    sample_markdown_file,
) -> dict:
    resp = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": sample_markdown_file.name,
            "reason": "stage before processing",
            "wait": False,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def test_add_resource_stages_and_dedupes_same_upload(
    client: httpx.AsyncClient,
    service,
    sample_markdown_file,
    upload_temp_dir,
    monkeypatch,
):
    monkeypatch.setattr(service.resources, "_resource_staging_delay_seconds", lambda: 3600.0)

    first = await _stage_upload(client, sample_markdown_file)
    second = await _stage_upload(client, sample_markdown_file)

    assert first["staged"] is True
    assert first["stage"] == "staged"
    assert second["idempotent"] is True
    assert second["task_id"] == first["task_id"]
    assert second["root_uri"] == first["root_uri"]

    task_resp = await client.get(f"/api/v1/tasks/{first['task_id']}")
    assert task_resp.status_code == 200, task_resp.text
    task = task_resp.json()["result"]
    assert task["status"] == "pending"
    assert task["stage"] == "staged"
    assert task["result"]["root_uri"] == first["root_uri"]


async def test_trigger_staged_resource_starts_task(
    client: httpx.AsyncClient,
    service,
    sample_markdown_file,
    upload_temp_dir,
    monkeypatch,
):
    monkeypatch.setattr(service.resources, "_resource_staging_delay_seconds", lambda: 3600.0)
    staged = await _stage_upload(client, sample_markdown_file)

    async def fake_run_add_resource_task(
        task_id,
        *,
        ctx,
        add_kwargs,
        resource_lock,
        cleanup_paths=None,
    ):
        from openviking.service.task_tracker import get_task_tracker

        tracker = get_task_tracker()
        await tracker.start(
            task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            stage="processing",
        )
        await tracker.complete(
            task_id,
            {"root_uri": add_kwargs["to"]},
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        await resource_lock.close()
        service.resources._cleanup_staged_paths(cleanup_paths or [])

    monkeypatch.setattr(
        service.resources,
        "_run_add_resource_task",
        fake_run_add_resource_task,
    )

    trigger_resp = await client.post(f"/api/v1/tasks/{staged['task_id']}/trigger")
    assert trigger_resp.status_code == 200, trigger_resp.text
    assert trigger_resp.json()["result"]["stage"] == "processing"

    await asyncio.sleep(0)

    task_resp = await client.get(f"/api/v1/tasks/{staged['task_id']}")
    task = task_resp.json()["result"]
    assert task["status"] == "completed"
    assert task["result"]["root_uri"] == staged["root_uri"]
