"""Audit metadata must correlate requests without retaining their contents or keys."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from benchmark.aml.audit import _PrivateRotatingFileHandler, audit_event, logger, request_audit
from benchmark.aml.server import AMLSettings, OpenVikingBackend, create_app


def events(caplog):
    return [json.loads(record.message) for record in caplog.records if record.name == logger.name]


async def test_concurrent_requests_keep_separate_ids_and_omit_sensitive_content(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    sensitive = "PRIVATE QUERY CONTENT"

    async def find(**kwargs):
        await asyncio.sleep(0)
        return [{"uri": "viking://memory", "content": "PRIVATE MEMORY CONTENT"}]

    backend = SimpleNamespace(find=find)
    app = create_app(settings=AMLSettings(aml_api_key="secret-key"), backend=backend)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        responses = await asyncio.gather(
            *[
                client.post(
                    "/search?api_key=URL-SECRET",
                    headers={"Authorization": "Bearer secret-key", "X-AML-Trace-Id": "untrusted"},
                    json={"user_id": user, "query": sensitive, "top_k": 1},
                )
                for user in ["alice", "bob"]
            ]
        )
    rows = events(caplog)
    traces = [response.headers["X-AML-Trace-Id"] for response in responses]
    assert len(set(traces)) == 2 and "untrusted" not in traces
    for user, trace in zip(["alice", "bob"], traces, strict=True):
        request_events = [row for row in rows if row["trace_id"] == trace]
        assert [row["event"] for row in request_events] == [
            "request_received",
            "search_started",
            "search_completed",
            "request_handled",
        ]
        assert all(row["user_id"] == user for row in request_events if "user_id" in row)
        assert request_events[-1]["http_status"] == 200
        assert request_events[2]["result_count"] == 1
    for secret in ["secret-key", sensitive, "PRIVATE MEMORY CONTENT", "URL-SECRET"]:
        assert secret not in caplog.text


async def test_add_records_native_task_and_archive_without_messages(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    native = SimpleNamespace(
        initialize=AsyncMock(),
        close=AsyncMock(),
        batch_add_messages=AsyncMock(return_value={"added": 1}),
        commit_session=AsyncMock(return_value={"status": "accepted", "task_id": "native-task"}),
        get_task=AsyncMock(
            return_value={
                "status": "completed",
                "result": {"archive_uri": "viking://user/alice/archive_001"},
            }
        ),
    )
    settings = AMLSettings(aml_api_key="secret-key", openviking_api_key="native-key")
    backend = OpenVikingBackend(settings, client_factory=lambda user: native)
    app = create_app(settings=settings, backend=backend)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        response = await client.post(
            "/add",
            headers={"X-API-Key": "secret-key"},
            json={
                "request_id": "req-1",
                "user_id": "alice",
                "session_id": "s1",
                "messages": [{"role": "user", "content": "PRIVATE CHAT CONTENT"}],
            },
        )
    assert response.status_code == 200
    rows = events(caplog)
    completed = next(row for row in rows if row["event"] == "commit_completed")
    assert completed["native_task_id"] == "native-task"
    assert completed["archive_uri"] == "viking://user/alice/archive_001"
    assert completed["request_id"] == "req-1"
    assert rows[-1]["native_task_id"] == "native-task"
    assert rows[-1]["message_count"] == 1
    assert "PRIVATE CHAT CONTENT" not in caplog.text
    native.batch_add_messages.assert_awaited_once()
    native.commit_session.assert_awaited_once()


async def test_auth_rejection_and_backend_failure_have_safe_records(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    settings = AMLSettings(aml_api_key="secret-key", retry_attempts=1)
    native = SimpleNamespace(
        initialize=AsyncMock(),
        close=AsyncMock(),
        find=AsyncMock(side_effect=httpx.ReadTimeout("PRIVATE UPSTREAM ERROR")),
    )
    app = create_app(
        settings=settings, backend=OpenVikingBackend(settings, client_factory=lambda user: native)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        rejected = await client.get("/secret-key?token=PRIVATE-TOKEN")
        failed = await client.post(
            "/search",
            headers={"X-API-Key": "secret-key"},
            json={"query": "PRIVATE QUERY", "user_id": "alice", "top_k": 1},
        )
    assert rejected.status_code == 401 and failed.status_code == 503
    rows = events(caplog)
    assert rows[0]["path"] == "<unmatched>"
    assert any(row.get("http_status") == 401 for row in rows)
    assert any(row.get("http_status") == 503 for row in rows)
    attempt = next(row for row in rows if row["event"] == "backend_attempt_failed")
    assert attempt["error_type"] == "ReadTimeout" and attempt["will_retry"] is False
    for secret in ["secret-key", "PRIVATE-TOKEN", "PRIVATE UPSTREAM ERROR", "PRIVATE QUERY"]:
        assert secret not in caplog.text


def test_metadata_redaction_and_private_bounded_rotation(tmp_path):
    path = tmp_path / "audit.jsonl"
    handler = _PrivateRotatingFileHandler(path, maxBytes=450, backupCount=2, encoding="utf-8")
    logger.addHandler(handler)
    try:
        with request_audit(secrets=("secret-key",), trace_id="test"):
            for i in range(20):
                audit_event("probe", request_id="secret-key\n" + "x" * 1000, sequence=i)
    finally:
        logger.removeHandler(handler)
        handler.close()
    files = list(tmp_path.iterdir())
    assert len(files) == 3
    for file in files:
        assert file.stat().st_mode & 0o777 == 0o600
        for line in file.read_text().splitlines():
            row = json.loads(line)
            assert "secret-key" not in line and "[REDACTED]" in row["request_id"]
            assert len(row["request_id"]) <= 512
