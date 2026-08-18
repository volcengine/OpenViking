# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
import time

import pytest

from openviking.usage_reporter.file_log_sink import FileLogUsageSink


class FakeUsageEvent:
    def __init__(
        self,
        *,
        event_id: str = "ue_recall",
        event_type: str = "memory.recalled",
        session_id: str = "session-1",
        resource_uri: str = ("viking://user/default/memories/experiences/生成请假邮件通用模版.md"),
    ) -> None:
        self._record = {
            "schema_version": "v1",
            "event_id": event_id,
            "event_type": event_type,
            "account_id": "default",
            "user_id": "default",
            "session_id": session_id,
            "task_id": "task-1",
            "occurred_at": "2026-08-05T19:30:00+08:00",
            "resource_uri": resource_uri,
            "resource_type": "experience",
            "evidence": {
                "archive_uri": ("viking://user/user-1/sessions/session-1/history/archive_001"),
                "message_id": "msg-1",
                "tool_call_id": "call-1",
                "tool_name": "find",
            },
            "attributes": {"note": "contains spaces\tand tabs"},
        }

    def to_dict(self) -> dict[str, object]:
        return dict(self._record)


def _parse_line(line: str) -> dict[str, object]:
    return json.loads(line)


@pytest.fixture(autouse=True)
def _resource_id(monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")


@pytest.mark.asyncio
async def test_file_log_sink_writes_usage_event_record(tmp_path):
    log_path = tmp_path / "dedicated" / "usage.log"
    sink = FileLogUsageSink(path=log_path)

    try:
        await sink.write(events=[FakeUsageEvent()])
    finally:
        sink.close()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert _parse_line(lines[0]) == {
        "event_time": "2026-08-05 11:30:00",
        "tenant_id": (
            "resource_id:ov-test;account_id:default;user_id:default;resource_uri:"
            "viking://user/default/memories/experiences/生成请假邮件通用模版.md"
        ),
        "event_name": "experience.recall.count",
        "object_id": "ue_recall",
        "count": 1,
        "tags": {"resource_type": "experience"},
    }


@pytest.mark.asyncio
async def test_file_log_sink_maps_injection_event_name(tmp_path):
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path)

    try:
        await sink.write(events=[FakeUsageEvent(event_type="memory.injected")])
    finally:
        sink.close()

    record = _parse_line(log_path.read_text(encoding="utf-8").strip())
    assert record["event_name"] == "experience.inject.count"


@pytest.mark.asyncio
async def test_file_log_sink_preserves_tenant_id_values(tmp_path):
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path)

    try:
        await sink.write(
            events=[
                FakeUsageEvent(
                    resource_uri=("viking://user/user-1/memories/experiences/exchange=delivered.md")
                )
            ]
        )
    finally:
        sink.close()

    record = _parse_line(log_path.read_text(encoding="utf-8").strip())
    assert record["tenant_id"] == (
        "resource_id:ov-test;account_id:default;user_id:default;resource_uri:"
        "viking://user/user-1/memories/experiences/exchange=delivered.md"
    )


@pytest.mark.asyncio
async def test_file_log_sink_uses_tenant_and_object_as_deduplication_key(tmp_path, monkeypatch):
    event = FakeUsageEvent(event_id="ue_same_event")

    monkeypatch.setenv("OV_RESOURCE_ID", "ov-first")
    first = FileLogUsageSink(path=tmp_path / "first.log")
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-second")
    second = FileLogUsageSink(path=tmp_path / "second.log")
    try:
        await first.write(events=[event])
        await second.write(events=[event])
    finally:
        first.close()
        second.close()

    first_record = _parse_line((tmp_path / "first.log").read_text(encoding="utf-8").strip())
    second_record = _parse_line((tmp_path / "second.log").read_text(encoding="utf-8").strip())

    assert first_record["object_id"] == second_record["object_id"] == "ue_same_event"
    assert first_record["tenant_id"] != second_record["tenant_id"]
    assert (first_record["tenant_id"], first_record["object_id"]) != (
        second_record["tenant_id"],
        second_record["object_id"],
    )


@pytest.mark.asyncio
async def test_file_log_sink_preserves_records_when_workers_roll_over(tmp_path):
    log_path = tmp_path / "usage.log"
    first = FileLogUsageSink(path=log_path)
    second = FileLogUsageSink(path=log_path)

    try:
        await first.write(events=[FakeUsageEvent(event_id="ue_first_before")])
        await second.write(events=[FakeUsageEvent(event_id="ue_second_before")])

        first._handler.rolloverAt = 0
        second._handler.rolloverAt = 0
        await first.write(events=[FakeUsageEvent(event_id="ue_first_after")])
        await second.write(events=[FakeUsageEvent(event_id="ue_second_after")])
    finally:
        first.close()
        second.close()

    object_ids = []
    for path in tmp_path.glob("usage.log*"):
        for line in path.read_text(encoding="utf-8").splitlines():
            object_ids.append(_parse_line(line)["object_id"])
    assert sorted(object_ids) == [
        "ue_first_after",
        "ue_first_before",
        "ue_second_after",
        "ue_second_before",
    ]


@pytest.mark.asyncio
async def test_closed_handle_preserves_overdue_rollover_deadline(tmp_path):
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path)

    try:
        await sink.write(events=[FakeUsageEvent(event_id="ue_before")])
        sink._handler.stream.close()
        sink._handler.stream = None
        sink._handler.rolloverAt = int(time.time()) - 1

        await sink.write(events=[FakeUsageEvent(event_id="ue_after")])
    finally:
        sink.close()

    log_files = list(tmp_path.glob("usage.log*"))
    assert len(log_files) == 2
    object_ids = []
    for path in log_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            object_ids.append(_parse_line(line)["object_id"])
    assert sorted(object_ids) == ["ue_after", "ue_before"]


def test_file_log_sink_uses_hourly_utc_rotation(tmp_path):
    sink = FileLogUsageSink(
        path=tmp_path / "usage.log",
        rotation_interval_hours=1,
        backup_count=168,
    )

    try:
        assert sink._handler.when == "H"
        assert sink._handler.interval == 60 * 60
        assert sink._handler.backupCount == 168
        assert sink._handler.utc is True
        assert sink._handler.suffix == "%Y-%m-%d_%H"
    finally:
        sink.close()


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
    ],
)
def test_file_log_sink_validates_config(kwargs, message):
    with pytest.raises(ValueError, match=message):
        FileLogUsageSink(**kwargs)


def test_file_log_sink_requires_resource_id(tmp_path, monkeypatch):
    monkeypatch.delenv("OV_RESOURCE_ID")

    with pytest.raises(ValueError, match="OV_RESOURCE_ID is required"):
        FileLogUsageSink(path=tmp_path / "usage.log")


def test_file_log_sink_reads_configured_resource_id_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOM_RESOURCE_ID", "ov-custom")
    sink = FileLogUsageSink(
        path=tmp_path / "usage.log",
        resource_id_env="CUSTOM_RESOURCE_ID",
    )
    try:
        assert sink._resource_id == "ov-custom"
    finally:
        sink.close()


@pytest.mark.asyncio
async def test_file_log_sink_rejects_unsupported_event(tmp_path):
    sink = FileLogUsageSink(path=tmp_path / "usage.log")

    try:
        with pytest.raises(ValueError, match="unsupported usage event type"):
            await sink.write(events=[FakeUsageEvent(event_type="memory.unknown")])
    finally:
        sink.close()


@pytest.mark.asyncio
async def test_file_log_sink_requires_event_id(tmp_path):
    sink = FileLogUsageSink(path=tmp_path / "usage.log")

    try:
        with pytest.raises(ValueError, match="event_id is required"):
            await sink.write(events=[FakeUsageEvent(event_id="")])
    finally:
        sink.close()
