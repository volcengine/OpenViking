# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from openviking.usage_reporter.http_sink import HttpUsageSink


class FakeUsageEvent:
    def __init__(
        self,
        event_id: str,
        session_id: str,
        attributes: dict[str, object] | None = None,
        *,
        event_type: str = "memory.recalled",
        occurred_at: str = "2026-07-27T04:00:00Z",
        resource_uri: str = "viking://user/user-1/memories/experiences/exchange.md",
        resource_type: str = "experience",
        task_id: str | None = None,
        evidence: dict[str, object] | None = None,
    ) -> None:
        self._record = {
            "schema_version": "v1",
            "event_id": event_id,
            "event_type": event_type,
            "account_id": "2101858484",
            "user_id": "user-1",
            "session_id": session_id,
            "task_id": task_id,
            "occurred_at": occurred_at,
            "resource_uri": resource_uri,
            "resource_type": resource_type,
            "evidence": evidence or {},
            "attributes": attributes or {},
        }

    def to_dict(self) -> dict[str, object]:
        return dict(self._record)


class UsageRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        content_length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(content_length))
        self.server.requests.append(body)
        self.server.resource_ids.append(self.headers["V-Resource-Id"])
        response_status = self.server.response_statuses.pop(0)
        self.send_response(response_status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        return


class UsageHTTPServer(ThreadingHTTPServer):
    requests: list[dict[str, object]]
    resource_ids: list[str]
    response_statuses: list[int]


def wait_until(predicate: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class TestHttpUsageSink:
    def test_write_persists_recall_as_count_record(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                    )
                    asyncio.run(
                        sink.write(
                            events=[
                                FakeUsageEvent(
                                    "ue_recall",
                                    "session-1",
                                    {"rank": 1},
                                    task_id="task-1",
                                    evidence={
                                        "archive_uri": (
                                            "viking://user/user-1/sessions/session-1/"
                                            "history/archive_001"
                                        ),
                                        "message_id": "msg-1",
                                        "tool_call_id": "call-1",
                                        "tool_name": "search_experience",
                                    },
                                )
                            ]
                        )
                    )

            pending_path = next((outbox_dir / "pending").glob("*.json"))
            payload = json.loads(pending_path.read_text(encoding="utf-8"))
            assert payload["events"] == [
                {
                    "CountName": "experience.recall.count",
                    "OpType": "add",
                    "amount": 1.0,
                    "timestamp": 1785124800000,
                    "uniqueId": "ue_recall",
                    "tags": {
                        "account_id": "2101858484",
                        "user_id": "user-1",
                        "resource_uri": ("viking://user/user-1/memories/experiences/exchange.md"),
                        "resource_type": "experience",
                    },
                    "extra": {
                        "session_id": "session-1",
                        "task_id": "task-1",
                        "archive_uri": (
                            "viking://user/user-1/sessions/session-1/history/archive_001"
                        ),
                        "message_id": "msg-1",
                        "tool_call_id": "call-1",
                        "tool_name": "search_experience",
                        "attributes": {"rank": 1},
                    },
                }
            ]

    def test_write_maps_injected_event_to_count_record(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                    )
                    asyncio.run(
                        sink.write(
                            events=[
                                FakeUsageEvent(
                                    "ue_inject",
                                    "session-1",
                                    event_type="memory.injected",
                                )
                            ]
                        )
                    )

            pending_path = next((outbox_dir / "pending").glob("*.json"))
            payload = json.loads(pending_path.read_text(encoding="utf-8"))
            assert payload["events"][0]["CountName"] == "experience.inject.count"

    def test_write_omits_empty_optional_count_record_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                    )
                    asyncio.run(sink.write(events=[FakeUsageEvent("ue_minimal", "session-1")]))

            pending_path = next((outbox_dir / "pending").glob("*.json"))
            payload = json.loads(pending_path.read_text(encoding="utf-8"))
            assert payload["events"][0]["extra"] == {"session_id": "session-1"}

    def test_write_rejects_unknown_usage_event_type(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(Path(data_dir) / ".usage_outbox"),
                    )
                    with pytest.raises(
                        ValueError,
                        match="unsupported usage event type: memory.unknown",
                    ):
                        asyncio.run(
                            sink.write(
                                events=[
                                    FakeUsageEvent(
                                        "ue_unknown",
                                        "session-1",
                                        event_type="memory.unknown",
                                    )
                                ]
                            )
                        )

    def test_write_atomically_persists_stable_batch(self) -> None:
        events = [
            FakeUsageEvent("ue_1", "session-1"),
            FakeUsageEvent("ue_2", "session-1"),
        ]
        expected_digest = hashlib.sha256(b"ue_1\nue_2").hexdigest()

        with tempfile.TemporaryDirectory() as data_dir:
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/api/openviking/ReportOpenVikingUsage",
                        outbox_dir=str(Path(data_dir) / ".usage_outbox"),
                    )
                    asyncio.run(sink.write(events=events))

            pending_files = list((Path(data_dir) / ".usage_outbox" / "pending").glob("*.json"))
            temporary_files = list((Path(data_dir) / ".usage_outbox" / "pending").glob("*.tmp"))

            assert len(pending_files) == 1
            assert temporary_files == []
            payload = json.loads(pending_files[0].read_text(encoding="utf-8"))
            assert payload["schema_version"] == "v1"
            assert payload["resource_id"] == "ov-test"
            assert payload["batch_id"] == f"ub_{expected_digest}"
            assert [event["uniqueId"] for event in payload["events"]] == [
                "ue_1",
                "ue_2",
            ]
            assert all("prefix" not in event for event in payload["events"])

    def test_write_splits_batches_before_http_limit(self) -> None:
        events = [
            FakeUsageEvent("ue_large_1", "session-large", {"text": "a" * 700}),
            FakeUsageEvent("ue_large_2", "session-large", {"text": "b" * 700}),
        ]

        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                        max_batch_bytes=1024,
                    )
                    asyncio.run(sink.write(events=events))

            batches = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (outbox_dir / "pending").glob("*.json")
            ]
            assert len(batches) == 2
            assert sorted(len(batch["events"]) for batch in batches) == [1, 1]

    def test_worker_retries_then_deletes_acknowledged_batch(self) -> None:
        server = UsageHTTPServer(("127.0.0.1", 0), UsageRequestHandler)
        server.requests = []
        server.resource_ids = []
        server.response_statuses = [503, 200]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as data_dir:
                outbox_dir = Path(data_dir) / ".usage_outbox"
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                    sink = HttpUsageSink(
                        endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                        outbox_dir=str(outbox_dir),
                        request_timeout_seconds=0.5,
                        retry_base_seconds=0.01,
                        retry_max_seconds=0.02,
                    )
                    asyncio.run(sink.write(events=[FakeUsageEvent("ue_retry", "session-retry")]))
                    wait_until(lambda: len(server.requests) == 2)
                    wait_until(
                        lambda: (
                            not list((outbox_dir / "pending").glob("*.json"))
                            and not list((outbox_dir / "inflight").glob("*.json"))
                        )
                    )
                    sink.close()

                assert [request["batch_id"] for request in server.requests] == [
                    server.requests[0]["batch_id"],
                    server.requests[0]["batch_id"],
                ]
                assert server.resource_ids == ["ov-test", "ov-test"]
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)

    def test_worker_accepts_any_successful_http_status(self) -> None:
        server = UsageHTTPServer(("127.0.0.1", 0), UsageRequestHandler)
        server.requests = []
        server.resource_ids = []
        server.response_statuses = [204]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as data_dir:
                outbox_dir = Path(data_dir) / ".usage_outbox"
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                    sink = HttpUsageSink(
                        endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                        outbox_dir=str(outbox_dir),
                        request_timeout_seconds=0.5,
                    )
                    asyncio.run(sink.write(events=[FakeUsageEvent("ue_204", "session-success")]))
                    wait_until(lambda: len(server.requests) == 1)
                    wait_until(
                        lambda: (
                            not list((outbox_dir / "pending").glob("*.json"))
                            and not list((outbox_dir / "inflight").glob("*.json"))
                        )
                    )
                    sink.close()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)

    def test_outbox_capacity_evicts_oldest_pending_batches(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                        max_outbox_bytes=1800,
                    )
                    for index in range(4):
                        asyncio.run(
                            sink.write(
                                events=[
                                    FakeUsageEvent(
                                        f"ue_capacity_{index}",
                                        "session-capacity",
                                        {"text": str(index) * 600},
                                    )
                                ]
                            )
                        )

            pending_files = list((outbox_dir / "pending").glob("*.json"))
            total_bytes = sum(path.stat().st_size for path in pending_files)
            persisted_ids = {
                event["uniqueId"]
                for path in pending_files
                for event in json.loads(path.read_text(encoding="utf-8"))["events"]
            }
            assert total_bytes <= 1800
            assert "ue_capacity_0" not in persisted_ids
            assert "ue_capacity_3" in persisted_ids

    def test_outbox_capacity_counts_inflight_without_evicting_it(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                        max_outbox_bytes=1800,
                    )
                    inflight_path = outbox_dir / "inflight" / f"{sink._outbox_scope}_active.json"
                    inflight_path.write_bytes(b"x" * 1000)
                    asyncio.run(
                        sink.write(
                            events=[
                                FakeUsageEvent(
                                    "ue_while_inflight",
                                    "session-capacity",
                                    {"text": "x" * 900},
                                )
                            ]
                        )
                    )

            assert inflight_path.exists()
            assert list((outbox_dir / "pending").glob("*.json")) == []

    def test_default_outbox_directory_uses_current_user_home(self) -> None:
        with tempfile.TemporaryDirectory() as home_dir:
            expected = Path(home_dir) / ".openviking" / "data" / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(Path, "home", return_value=Path(home_dir)):
                    with mock.patch.object(HttpUsageSink, "_start_worker"):
                        sink = HttpUsageSink(endpoint="http://127.0.0.1:1/usage")

            assert sink._outbox_dir == expected
            assert expected.is_dir()

    def test_shared_outbox_root_isolated_by_endpoint_and_resource(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_root = Path(data_dir) / ".usage_outbox"
            with mock.patch.object(HttpUsageSink, "_start_worker"):
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "resource-a"}, clear=False):
                    sink_a = HttpUsageSink(
                        endpoint="http://collector-a.example/usage",
                        outbox_dir=str(outbox_root),
                    )
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "resource-b"}, clear=False):
                    other_resource_sink = HttpUsageSink(
                        endpoint="http://collector-a.example/usage",
                        outbox_dir=str(outbox_root),
                    )
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "resource-a"}, clear=False):
                    other_endpoint_sink = HttpUsageSink(
                        endpoint="http://collector-b.example/usage",
                        outbox_dir=str(outbox_root),
                    )

                asyncio.run(sink_a.write(events=[FakeUsageEvent("ue_a", "session-a")]))
                sink_a._post_payload = mock.Mock(return_value=200)
                other_resource_sink._post_payload = mock.Mock(return_value=200)
                other_endpoint_sink._post_payload = mock.Mock(return_value=200)

                assert other_resource_sink._handle_next_batch() is False
                other_resource_sink._post_payload.assert_not_called()
                assert other_endpoint_sink._handle_next_batch() is False
                other_endpoint_sink._post_payload.assert_not_called()
                assert sink_a._handle_next_batch() is True
                sink_a._post_payload.assert_called_once()

    def test_worker_splits_payload_rejected_as_too_large(self) -> None:
        server = UsageHTTPServer(("127.0.0.1", 0), UsageRequestHandler)
        server.requests = []
        server.resource_ids = []
        server.response_statuses = [413, 200, 200]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as data_dir:
                outbox_dir = Path(data_dir) / ".usage_outbox"
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                    sink = HttpUsageSink(
                        endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                        outbox_dir=str(outbox_dir),
                        request_timeout_seconds=0.5,
                    )
                    asyncio.run(
                        sink.write(
                            events=[
                                FakeUsageEvent("ue_left", "session-split"),
                                FakeUsageEvent("ue_right", "session-split"),
                            ]
                        )
                    )
                    wait_until(lambda: len(server.requests) == 3)
                    wait_until(
                        lambda: (
                            not list((outbox_dir / "pending").glob("*.json"))
                            and not list((outbox_dir / "inflight").glob("*.json"))
                        )
                    )
                    sink.close()

                original_batch_id = server.requests[0]["batch_id"]
                child_batch_ids = {request["batch_id"] for request in server.requests[1:]}
                assert [len(request["events"]) for request in server.requests] == [2, 1, 1]
                assert original_batch_id not in child_batch_ids
                assert len(child_batch_ids) == 2
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)

    def test_split_replaces_parent_without_evicting_child_batches(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            events = [
                FakeUsageEvent("ue_split_left", "session-split", {"text": "a" * 500}),
                FakeUsageEvent("ue_split_right", "session-split", {"text": "b" * 500}),
            ]
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                        max_outbox_bytes=10_000,
                    )
                    asyncio.run(sink.write(events=events))

                    parent_path = next((outbox_dir / "pending").glob("*.json"))
                    payload = json.loads(parent_path.read_text(encoding="utf-8"))
                    inflight_path = outbox_dir / "inflight" / parent_path.name
                    os.replace(parent_path, inflight_path)
                    child_size = sum(
                        sink._encoded_payload_size([event]) for event in payload["events"]
                    )
                    sink._max_outbox_bytes = child_size + 50

                    sink._split_batch(inflight_path, payload)

            children = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (outbox_dir / "pending").glob("*.json")
            ]
            assert not inflight_path.exists()
            assert len(children) == 2
            assert {event["uniqueId"] for child in children for event in child["events"]} == {
                "ue_split_left",
                "ue_split_right",
            }

    def test_worker_moves_unrecoverable_payload_to_dead_letter(self) -> None:
        server = UsageHTTPServer(("127.0.0.1", 0), UsageRequestHandler)
        server.requests = []
        server.resource_ids = []
        server.response_statuses = [422]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as data_dir:
                outbox_dir = Path(data_dir) / ".usage_outbox"
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                    sink = HttpUsageSink(
                        endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                        outbox_dir=str(outbox_dir),
                    )
                    asyncio.run(sink.write(events=[FakeUsageEvent("ue_bad", "session-bad")]))
                    wait_until(lambda: len(list((outbox_dir / "dead_letter").glob("*.json"))) == 1)
                    sink.close()

                assert len(server.requests) == 1
                assert not list((outbox_dir / "pending").glob("*.json"))
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)

    def test_startup_recovers_only_stale_inflight_batch(self) -> None:
        server = UsageHTTPServer(("127.0.0.1", 0), UsageRequestHandler)
        server.requests = []
        server.resource_ids = []
        server.response_statuses = [200]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as data_dir:
                outbox_dir = Path(data_dir) / ".usage_outbox"
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                    with mock.patch.object(HttpUsageSink, "_start_worker"):
                        first_sink = HttpUsageSink(
                            endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                            outbox_dir=str(outbox_dir),
                        )
                        asyncio.run(
                            first_sink.write(events=[FakeUsageEvent("ue_stale", "session-stale")])
                        )
                    pending_path = next((outbox_dir / "pending").glob("*.json"))
                    inflight_path = outbox_dir / "inflight" / pending_path.name
                    os.replace(pending_path, inflight_path)
                    stale_time = time.time() - 10
                    os.utime(inflight_path, (stale_time, stale_time))

                    second_sink = HttpUsageSink(
                        endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                        outbox_dir=str(outbox_dir),
                        request_timeout_seconds=0.5,
                        inflight_lease_seconds=1,
                    )
                    wait_until(lambda: len(server.requests) == 1)
                    wait_until(lambda: not inflight_path.exists())
                    second_sink.close()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)

    def test_worker_recovers_inflight_batch_when_lease_expires_after_startup(self) -> None:
        server = UsageHTTPServer(("127.0.0.1", 0), UsageRequestHandler)
        server.requests = []
        server.resource_ids = []
        server.response_statuses = [200]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as data_dir:
                outbox_dir = Path(data_dir) / ".usage_outbox"
                with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                    with mock.patch.object(HttpUsageSink, "_start_worker"):
                        first_sink = HttpUsageSink(
                            endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                            outbox_dir=str(outbox_dir),
                        )
                        asyncio.run(
                            first_sink.write(events=[FakeUsageEvent("ue_fresh", "session-fresh")])
                        )
                    pending_path = next((outbox_dir / "pending").glob("*.json"))
                    inflight_path = outbox_dir / "inflight" / pending_path.name
                    os.replace(pending_path, inflight_path)

                    second_sink = HttpUsageSink(
                        endpoint=f"http://127.0.0.1:{server.server_port}/usage",
                        outbox_dir=str(outbox_dir),
                        request_timeout_seconds=0.01,
                        inflight_lease_seconds=0.05,
                    )
                    wait_until(lambda: len(server.requests) == 1)
                    wait_until(lambda: not inflight_path.exists())
                    second_sink.close()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)

    def test_worker_keeps_batch_after_unexpected_delivery_exception(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                sink = HttpUsageSink(
                    endpoint="http://127.0.0.1:1/usage",
                    outbox_dir=str(outbox_dir),
                    retry_base_seconds=0.01,
                    retry_max_seconds=0.02,
                )
                attempts = 0

                def flaky_post(payload: dict[str, object]) -> int:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("unexpected delivery failure")
                    return 200

                sink._post_payload = flaky_post
                with mock.patch(
                    "openviking.usage_reporter.http_sink.logger.exception"
                ) as log_exception:
                    asyncio.run(
                        sink.write(events=[FakeUsageEvent("ue_exception", "session-exception")])
                    )
                    wait_until(lambda: attempts == 2)
                    wait_until(
                        lambda: (
                            not list((outbox_dir / "pending").glob("*.json"))
                            and not list((outbox_dir / "inflight").glob("*.json"))
                        )
                    )
                    sink.close()
                    assert log_exception.call_count == 1
                    assert (
                        "usage delivery failed unexpectedly"
                        in log_exception.call_args.args[0].lower()
                    )

    def test_claiming_pending_batch_refreshes_inflight_lease(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            outbox_dir = Path(data_dir) / ".usage_outbox"
            with mock.patch.dict(os.environ, {"OV_RESOURCE_ID": "ov-test"}, clear=False):
                with mock.patch.object(HttpUsageSink, "_start_worker"):
                    sink = HttpUsageSink(
                        endpoint="http://127.0.0.1:1/usage",
                        outbox_dir=str(outbox_dir),
                        request_timeout_seconds=0.01,
                        inflight_lease_seconds=0.05,
                    )
                    asyncio.run(sink.write(events=[FakeUsageEvent("ue_claim", "session-claim")]))
                    pending_path = next((outbox_dir / "pending").glob("*.json"))
                    stale_time = time.time() - 60
                    os.utime(pending_path, (stale_time, stale_time))
                    claimed_mtime = 0.0

                    def record_claim(_payload: dict[str, object]) -> int:
                        nonlocal claimed_mtime
                        inflight_path = outbox_dir / "inflight" / pending_path.name
                        claimed_mtime = inflight_path.stat().st_mtime
                        return 200

                    sink._post_payload = record_claim
                    assert sink._handle_next_batch() is True

            assert claimed_mtime >= time.time() - 1
