# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from openviking.usage_reporter.file_log_sink import FileLogUsageSink
from openviking.usage_reporter.models import UsageEvent


@pytest.fixture
def recall_event():
    return UsageEvent(
        event_type="memory.recalled",
        resource_uri="viking://user/default/memories/experiences/请假=delivered.md",
        resource_type="experience",
        account_id="default",
        user_id="default",
        session_id="session-1",
        occurred_at="2026-08-05T19:30:00+08:00",
        event_id="ue_recall",
    )


@pytest.fixture(autouse=True)
def _resource_id(monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")


@pytest.mark.asyncio
async def test_file_log_sink_writes_usage_event_record(tmp_path, monkeypatch, recall_event):
    log_path = tmp_path / "dedicated" / "usage.log"
    other_path = tmp_path / "custom.log"
    monkeypatch.setenv("CUSTOM_RESOURCE_ID", "ov-other")
    sink = FileLogUsageSink(path=log_path)
    other_sink = FileLogUsageSink(path=other_path, resource_id_env="CUSTOM_RESOURCE_ID")
    injection = replace(recall_event, event_type="memory.injected", event_id="ue_inject")
    try:
        await sink.write(events=[recall_event, injection])
        await other_sink.write(events=[recall_event])
    finally:
        sink.close()
        other_sink.close()

    tenant = f"account_id:default;user_id:default;resource_uri:{recall_event.resource_uri}"
    recall = {
        "event_time": "2026-08-05 11:30:00",
        "tenant_id": f"resource_id:ov-test;{tenant}",
        "event_name": "experience.recall.count",
        "object_id": "ue_recall",
        "count": 1,
        "tags": {"resource_type": "experience"},
    }
    assert [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] == [
        recall,
        recall | {"event_name": "experience.inject.count", "object_id": "ue_inject"},
    ]
    assert json.loads(other_path.read_text(encoding="utf-8")) == recall | {
        "tenant_id": f"resource_id:ov-other;{tenant}",
    }


@pytest.mark.asyncio
async def test_file_log_sink_preserves_records_when_workers_roll_over(tmp_path, recall_event):
    log_path = tmp_path / "usage.log"
    first = FileLogUsageSink(path=log_path)
    second = FileLogUsageSink(path=log_path)

    try:
        await first.write(events=[replace(recall_event, event_id="ue_first_before")])
        await second.write(events=[replace(recall_event, event_id="ue_second_before")])

        first._handler.rolloverAt = 0
        second._handler.rolloverAt = 0
        await first.write(events=[replace(recall_event, event_id="ue_first_after")])
        await second.write(events=[replace(recall_event, event_id="ue_second_after")])
    finally:
        first.close()
        second.close()

    object_ids = []
    for path in tmp_path.glob("usage.log*"):
        for line in path.read_text(encoding="utf-8").splitlines():
            object_ids.append(json.loads(line)["object_id"])
    assert sorted(object_ids) == [
        "ue_first_after",
        "ue_first_before",
        "ue_second_after",
        "ue_second_before",
    ]


@pytest.mark.asyncio
async def test_closed_handle_preserves_overdue_rollover_deadline(tmp_path, recall_event):
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path, backup_count=1)
    deadline = int(time.time()) - 1
    archived = [
        tmp_path / f"usage.log.{datetime.fromtimestamp(at, timezone.utc):%Y-%m-%d_%H}"
        for at in (deadline - 7200, deadline - 3600)
    ]

    try:
        await sink.write(events=[replace(recall_event, event_id="ue_before")])
        if sink._handler.stream is not None:
            sink._handler.stream.close()
            sink._handler.stream = None
        sink._handler.rolloverAt = deadline - 3600
        await sink.write(events=[replace(recall_event, event_id="ue_after")])
        assert json.loads(archived[0].read_text(encoding="utf-8"))["object_id"] == "ue_before"
        assert json.loads(log_path.read_text(encoding="utf-8"))["object_id"] == "ue_after"

        sink._handler.rolloverAt = deadline
        await sink.write(events=[replace(recall_event, event_id="ue_current")])
    finally:
        sink.close()

    assert sorted(tmp_path.glob("usage.log*")) == [log_path, archived[1]]
    assert json.loads(archived[1].read_text(encoding="utf-8"))["object_id"] == "ue_after"
    assert json.loads(log_path.read_text(encoding="utf-8"))["object_id"] == "ue_current"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"path": ""}, "path is required"),
        (
            {"path": "usage.log", "rotation_interval_hours": 0},
            "rotation_interval_hours must be positive",
        ),
        (
            {"path": "usage.log", "backup_count": -1},
            "backup_count must be non-negative",
        ),
        (
            {"path": "usage.log", "resource_id_source": "pod"},
            "resource_id_source must be environment or account",
        ),
    ],
)
def test_file_log_sink_validates_config(kwargs, message):
    with pytest.raises(ValueError, match=message):
        FileLogUsageSink(**kwargs)


@pytest.mark.asyncio
async def test_account_resource_id_source_attributes_each_event_to_its_account(
    tmp_path, monkeypatch, recall_event
):
    monkeypatch.delenv("OV_RESOURCE_ID")
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path, resource_id_source="account")
    uri = "viking://user/default/memories/experiences/report.md"

    try:
        await sink.write(
            events=[
                replace(recall_event, event_id="ue_a", account_id="res-a", resource_uri=uri),
                replace(recall_event, event_id="ue_b", account_id="res-b", resource_uri=uri),
            ]
        )
    finally:
        sink.close()

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [(record["object_id"], record["tenant_id"]) for record in records] == [
        ("ue_a", f"resource_id:res-a;account_id:res-a;user_id:default;resource_uri:{uri}"),
        ("ue_b", f"resource_id:res-b;account_id:res-b;user_id:default;resource_uri:{uri}"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"account_id": " "}, "account_id is required"),
        ({"event_type": "memory.unknown"}, "unsupported usage event type"),
        ({"event_id": " "}, "event_id is required"),
    ],
    ids=["missing-account", "unsupported-event", "blank-id"],
)
async def test_file_log_sink_rejects_invalid_batch(tmp_path, recall_event, changes, message):
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path, resource_id_source="account")
    try:
        with pytest.raises(ValueError, match=message):
            await sink.write(events=[recall_event, replace(recall_event, **changes)])
    finally:
        sink.close()
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""


def test_file_log_sink_requires_resource_id(tmp_path, monkeypatch):
    monkeypatch.delenv("OV_RESOURCE_ID")

    with pytest.raises(ValueError, match="OV_RESOURCE_ID is required"):
        FileLogUsageSink(path=tmp_path / "usage.log")
