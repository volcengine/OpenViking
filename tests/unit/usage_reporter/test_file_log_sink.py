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
        resource_uri: str = "viking://user/user-1/memories/experiences/exchange.md",
    ) -> None:
        self._record = {
            "schema_version": "v1",
            "event_id": event_id,
            "event_type": event_type,
            "account_id": "2101858484",
            "user_id": "user-1",
            "session_id": session_id,
            "task_id": "task-1",
            "occurred_at": "2026-07-27T04:00:00Z",
            "resource_uri": resource_uri,
            "resource_type": "experience",
            "evidence": {
                "archive_uri": ("viking://user/user-1/sessions/session-1/history/archive_001"),
                "message_id": "msg-1",
                "tool_call_id": "call-1",
                "tool_name": "search_experience",
            },
            "attributes": {"note": "contains spaces\tand tabs"},
        }

    def to_dict(self) -> dict[str, object]:
        return dict(self._record)


def _parse_line(line: str) -> tuple[str, dict[str, object]]:
    record = json.loads(line)
    return record["key"], record["value"]


@pytest.mark.asyncio
async def test_file_log_sink_writes_original_kafka_key_and_value(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
    log_path = tmp_path / "dedicated" / "usage.log"
    sink = FileLogUsageSink(path=log_path)

    try:
        await sink.write(events=[FakeUsageEvent()])
    finally:
        sink.close()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    key, value = _parse_line(lines[0])
    assert key == "ov-test|2101858484|user-1|viking://user/user-1/memories/experiences/exchange.md"
    assert value == {
        "count_name": "experience.recall.count",
        "op_type": "add",
        "amount": 1.0,
        "timestamp": 1785124800000,
        "unique_id": "ue_recall",
        "tags": {
            "account_id": "2101858484",
            "user_id": "user-1",
            "resource_uri": "viking://user/user-1/memories/experiences/exchange.md",
            "resource_type": "experience",
        },
        "extra": {
            "archive_uri": "viking://user/user-1/sessions/session-1/history/archive_001",
            "message_id": "msg-1",
            "tool_call_id": "call-1",
            "tool_name": "search_experience",
            "session_id": "session-1",
            "task_id": "task-1",
            "attributes": {"note": "contains spaces\tand tabs"},
        },
        "prefix": "ov-test",
    }


@pytest.mark.asyncio
async def test_file_log_sink_message_key_falls_back_to_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
    log_path = tmp_path / "usage.log"
    sink = FileLogUsageSink(path=log_path)

    try:
        await sink.write(events=[FakeUsageEvent(resource_uri="", session_id="session-fallback")])
    finally:
        sink.close()

    key, _value = _parse_line(log_path.read_text(encoding="utf-8").strip())
    assert key == "ov-test|2101858484|user-1|session-fallback"


@pytest.mark.asyncio
async def test_file_log_sink_preserves_equals_signs_in_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov=test")
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

    key, value = _parse_line(log_path.read_text(encoding="utf-8").strip())
    assert key == (
        "ov=test|2101858484|user-1|viking://user/user-1/memories/experiences/exchange=delivered.md"
    )
    assert value["prefix"] == "ov=test"
    assert (
        value["tags"]["resource_uri"]
        == "viking://user/user-1/memories/experiences/exchange=delivered.md"
    )


@pytest.mark.asyncio
async def test_file_log_sink_preserves_records_when_workers_roll_over(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
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

    unique_ids = []
    for path in tmp_path.glob("usage.log*"):
        for line in path.read_text(encoding="utf-8").splitlines():
            _key, value = _parse_line(line)
            unique_ids.append(value["unique_id"])
    assert sorted(unique_ids) == [
        "ue_first_after",
        "ue_first_before",
        "ue_second_after",
        "ue_second_before",
    ]


@pytest.mark.asyncio
async def test_closed_handle_preserves_overdue_rollover_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
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
    unique_ids = []
    for path in log_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            _key, value = _parse_line(line)
            unique_ids.append(value["unique_id"])
    assert sorted(unique_ids) == ["ue_after", "ue_before"]


def test_file_log_sink_uses_hourly_utc_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
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
    ("environment", "kwargs", "message"),
    [
        ({}, {"path": "usage.log"}, "OV_RESOURCE_ID is required"),
        ({"OV_RESOURCE_ID": "ov-test"}, {"path": ""}, "path is required"),
        (
            {"OV_RESOURCE_ID": "ov-test"},
            {"path": "usage.log", "rotation_interval_hours": 0},
            "rotation_interval_hours must be positive",
        ),
        (
            {"OV_RESOURCE_ID": "ov-test"},
            {"path": "usage.log", "backup_count": -1},
            "backup_count must be non-negative",
        ),
    ],
)
def test_file_log_sink_validates_config(
    monkeypatch,
    environment,
    kwargs,
    message,
):
    monkeypatch.delenv("OV_RESOURCE_ID", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=message):
        FileLogUsageSink(**kwargs)


@pytest.mark.asyncio
async def test_file_log_sink_rejects_unsupported_event(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
    sink = FileLogUsageSink(path=tmp_path / "usage.log")

    try:
        with pytest.raises(ValueError, match="unsupported usage event type"):
            await sink.write(events=[FakeUsageEvent(event_type="memory.unknown")])
    finally:
        sink.close()


@pytest.mark.asyncio
async def test_file_log_sink_requires_event_id(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
    sink = FileLogUsageSink(path=tmp_path / "usage.log")

    try:
        with pytest.raises(ValueError, match="event_id is required"):
            await sink.write(events=[FakeUsageEvent(event_id="")])
    finally:
        sink.close()
