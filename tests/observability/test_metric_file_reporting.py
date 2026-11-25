"""Select the configured event export and verify local delivery."""

import asyncio
import json
from queue import Full
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.observability.events import try_publish_event
from openviking.observability.reporting import runtime
from openviking.observability.reporting.file_reporter import FileEventReporter
from openviking.server.api_keys.key_mgr import APIKeyManager
from openviking.server.config import EventReportingConfig


@pytest.mark.asyncio
async def test_file_reporting_preserves_events_without_kafka(tmp_path, monkeypatch):
    path = tmp_path / "metric_logs" / "metric.log"
    kafka_factory = Mock(side_effect=AssertionError("file output must not connect to Kafka"))
    monkeypatch.setattr(runtime, "KafkaEventReporter", kafka_factory)
    settings = EventReportingConfig(
        enabled=True,
        file_log={"path": str(path), "rotation_interval_hours": 1, "backup_count": 24},
        telemetry={
            "enabled": True,
            "deployment": {"account_id": "cloud", "resource_id": "library", "region": "local"},
        },
    )
    try:
        reporter = await runtime.start_reporting(settings)
        assert isinstance(reporter, FileEventReporter)
        try_publish_event("http.request", {"method": "GET", "route": "/health", "status": 200})
    finally:
        await runtime.stop_reporting()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_name"] == "http_request"
    assert rows[0]["account_id"] == "cloud"
    assert rows[0]["resource_id"] == "library"
    assert rows[0]["log_source"] == "ov"
    assert isinstance(rows[0]["timestamp"], int)
    assert rows[0]["event_id"]
    kafka_factory.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_kafka_takes_precedence_over_file_reporting(tmp_path, monkeypatch):
    kafka_reporter = Mock()
    kafka_factory = Mock(return_value=kafka_reporter)
    file_factory = Mock(side_effect=AssertionError("configured Kafka is the only output"))
    monkeypatch.setattr(runtime, "KafkaEventReporter", kafka_factory)
    monkeypatch.setattr(runtime, "FileEventReporter", file_factory)
    settings = EventReportingConfig(
        enabled=True,
        kafka={"bootstrap_servers": "broker:9092", "topic": "metric-log"},
        file_log={"path": str(tmp_path / "metric.log")},
        telemetry={"enabled": True},
    )
    try:
        assert await runtime.start_reporting(settings) is kafka_reporter
        try_publish_event("http.request", {"method": "GET", "route": "/health", "status": 200})
        kafka_reporter.report.assert_called_once()
    finally:
        await runtime.stop_reporting()
    kafka_factory.assert_called_once()
    file_factory.assert_not_called()


def test_file_reporting_keeps_json_types_and_escapes_newlines(tmp_path):
    path = tmp_path / "metric.log"
    reporter = FileEventReporter({"file_log": {"path": str(path)}})
    event = {
        "event_id": "stable",
        "source_type": "create_collection",
        "prompt_tokens": None,
        "completion_tokens": 0,
        "request_id": "request",
        "uri": "文件\n第二行",
    }
    assert reporter.report(event)
    reporter.close()
    assert len(path.read_text().splitlines()) == 1
    assert json.loads(path.read_text()) == event
    assert not reporter.report(event)


def test_file_reporting_rollover_preserves_all_events(tmp_path):
    path = tmp_path / "metric.log"
    reporter = FileEventReporter({"file_log": {"path": str(path)}})
    assert reporter.report({"event_id": "before"})
    reporter.flush()
    reporter._handler.rolloverAt = 0
    assert reporter.report({"event_id": "after"})
    reporter.close()
    ids = [
        json.loads(line)["event_id"]
        for file in tmp_path.glob("metric.log*")
        for line in file.read_text().splitlines()
    ]
    assert sorted(ids) == ["after", "before"]


def test_file_reporting_exposes_write_failure(tmp_path, monkeypatch):
    reporter = FileEventReporter({"file_log": {"path": str(tmp_path / "metric.log")}})

    def fail(record):
        raise OSError("disk unavailable")

    monkeypatch.setattr(reporter._handler, "handle", fail)
    assert reporter.report({"event_id": "write_failure"})
    with pytest.raises(RuntimeError, match="delivery incomplete"):
        reporter.flush()
    with pytest.raises(RuntimeError, match="lost events"):
        reporter.close()
    assert reporter.failed == 1
    assert reporter.sent == 0


def test_file_reporting_exposes_queue_overflow(tmp_path, monkeypatch):
    reporter = FileEventReporter({"file_log": {"path": str(tmp_path / "metric.log")}})
    monkeypatch.setattr(reporter.queue, "put_nowait", Mock(side_effect=Full))

    assert not reporter.report({"event_id": "overflow"})
    with pytest.raises(RuntimeError, match="delivery incomplete"):
        reporter.flush()
    with pytest.raises(RuntimeError, match="lost events"):
        reporter.close()
    assert reporter.dropped == 1
    assert reporter.failed == 0


@pytest.mark.asyncio
async def test_registry_change_returns_while_file_writer_is_blocked(tmp_path, monkeypatch):
    path = tmp_path / "metric.log"
    settings = EventReportingConfig(
        enabled=True, file_log={"path": str(path)}, telemetry={"enabled": True}
    )
    write_started = Event()
    release_write = Event()
    reporter = await runtime.start_reporting(settings)
    assert isinstance(reporter, FileEventReporter)
    handle_record = reporter._handler.handle

    def wait_to_write(record):
        write_started.set()
        if not release_write.wait(10):
            raise TimeoutError("test writer was not released")
        handle_record(record)

    monkeypatch.setattr(reporter._handler, "handle", wait_to_write)
    manager = APIKeyManager("root", AsyncMock(), issue_user_keys=False)
    try:
        await asyncio.wait_for(
            asyncio.to_thread(asyncio.run, manager.create_account("space", "admin")), timeout=5
        )
        assert await asyncio.to_thread(write_started.wait, 5)
        assert reporter.sent == 0
    finally:
        release_write.set()
        await runtime.stop_reporting()
    assert [json.loads(line)["event_name"] for line in path.read_text().splitlines()] == [
        "space_change",
        "membership_change",
    ]


@pytest.mark.asyncio
async def test_dashboard_lifecycle_and_usage_are_written_with_source_identity(tmp_path):
    target = tmp_path / "metric.log"
    settings = EventReportingConfig(
        enabled=True,
        file_log={"path": str(target)},
        telemetry={
            "enabled": True,
            "deployment": {"account_id": "cloud", "resource_id": "library", "region": "local"},
        },
    )
    store = AsyncMock()
    store.begin_deletion.side_effect = [
        ({"task_id": "delete"}, True),
        ({"task_id": "delete"}, False),
    ]
    manager = APIKeyManager("root", store, issue_user_keys=False)
    try:
        await runtime.start_reporting(settings)
        await manager.create_account("space", "admin")
        await manager.register_user("space", "alice")
        await manager.set_role("space", "alice", "admin")
        await manager.begin_deletion("space", "alice", task_id="delete")
        await manager.begin_deletion("space", "alice", task_id="retry")
        try_publish_event(
            "embedding.call",
            {
                "account_id": "space",
                "user_id": "admin",
                "source_type": "semantic_search",
                "prompt_tokens": 12,
            },
        )
    finally:
        await runtime.stop_reporting()
    events = [json.loads(line) for line in target.read_text().splitlines()]
    assert len(events) == 6
    assert [(row["event_name"], row["change_type"]) for row in events[:5]] == [
        ("space_change", "created"),
        ("membership_change", "created"),
        ("membership_change", "created"),
        ("membership_change", "updated"),
        ("membership_change", "deleted"),
    ]
    assert [row["ov_user_id"] for row in events[2:5]] == ["alice"] * 3
    assert events[3]["role"] == "admin"
    assert events[5]["usage_purpose"] == "search_query"
    assert events[5]["call_count"] == 1
    assert events[5]["prompt_tokens"] == 12
    assert all(row["ov_account_id"] == "space" and row["account_id"] == "cloud" for row in events)
