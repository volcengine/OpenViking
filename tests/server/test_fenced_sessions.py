# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP contract tests for protocol-v2 Alice fenced session writes."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from openviking.server.fenced_postgres import (
    fencing_database_url,
    validate_postgres_fencing_schema,
)
from openviking.server.routers import fenced_sessions as fenced_sessions_routes

ALICE_TOKEN = "test-openviking-alice-service-token-0001"
FENCED_PREFIX = "/api/v1/fenced"


def _session_id(seed: str) -> str:
    return "alice_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]


def _operation_id(seed: str) -> str:
    return "op-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _headers(token: str = ALICE_TOKEN) -> dict[str, str]:
    return {"X-OpenViking-Alice-Token": token}


def _owner_dsn() -> str:
    value = os.getenv("OPENVIKING_ALICE_FENCING_TEST_OWNER_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("PostgreSQL fencing test owner DSN is not configured")
    return value


def _base(
    *,
    token: int,
    operation_id: str,
    turn_id: str = "turn-1",
    scope: str = "tenant-1:customer-1",
) -> dict[str, Any]:
    return {
        "writer": "alice",
        "session_scope_id": scope,
        "turn_id": turn_id,
        "operation_id": operation_id,
        "fencing_token": token,
    }


def _create_body(
    session_id: str,
    *,
    token: int = 1,
    operation_id: str | None = None,
    turn_id: str = "turn-1",
    scope: str = "tenant-1:customer-1",
) -> dict[str, Any]:
    return {
        **_base(
            token=token,
            operation_id=operation_id or _operation_id(f"create:{session_id}"),
            turn_id=turn_id,
            scope=scope,
        ),
        "session_id": session_id,
    }


def _message_body(
    *,
    token: int,
    operation_id: str,
    content: str,
    turn_id: str = "turn-1",
    scope: str = "tenant-1:customer-1",
) -> dict[str, Any]:
    return {
        **_base(
            token=token,
            operation_id=operation_id,
            turn_id=turn_id,
            scope=scope,
        ),
        "role": "user",
        "content": content,
    }


def _operation(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    assert payload["status"] == "ok"
    result = payload["result"]
    assert isinstance(result, dict)
    return result


def _assert_pending(
    response: httpx.Response,
    *,
    operation_id: str,
    replayed: bool,
) -> dict[str, Any]:
    assert response.status_code == 202
    operation = _operation(response)
    assert operation["operation_id"] == operation_id
    assert operation["status"] in {"queued", "running"}
    assert operation["replayed"] is replayed
    assert operation["status_url"] == (f"{FENCED_PREFIX}/operations/{operation_id}")
    assert "result" not in operation
    return operation


def _assert_completed(
    response: httpx.Response,
    *,
    operation_id: str,
    replayed: bool,
) -> dict[str, Any]:
    assert response.status_code == 200
    operation = _operation(response)
    assert operation["operation_id"] == operation_id
    assert operation["status"] == "completed"
    assert operation["replayed"] is replayed
    assert operation["status_url"] == (f"{FENCED_PREFIX}/operations/{operation_id}")
    assert isinstance(operation["result"], dict)
    return operation


async def _wait_for_terminal(
    client: httpx.AsyncClient,
    response: httpx.Response,
    *,
    operation_id: str,
    replayed: bool = False,
    timeout_seconds: float = 10.0,
) -> httpx.Response:
    if response.status_code == 200:
        _assert_completed(
            response,
            operation_id=operation_id,
            replayed=replayed,
        )
        return response

    pending = _assert_pending(
        response,
        operation_id=operation_id,
        replayed=replayed,
    )
    status_url = str(pending["status_url"])
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        polled = await client.get(status_url, headers=_headers())
        if polled.status_code == 200:
            _assert_completed(
                polled,
                operation_id=operation_id,
                # A GET is a status read, not a duplicate submission.
                replayed=False,
            )
            return polled
        _assert_pending(
            polled,
            operation_id=operation_id,
            replayed=False,
        )
        await asyncio.sleep(0.02)
    raise AssertionError(f"fenced operation {operation_id} did not complete")


async def _post_create_and_wait(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    token: int = 1,
    operation_id: str | None = None,
    turn_id: str = "turn-1",
    scope: str = "tenant-1:customer-1",
) -> httpx.Response:
    resolved_operation_id = operation_id or _operation_id(f"create:{session_id}")
    response = await client.post(
        f"{FENCED_PREFIX}/sessions",
        headers=_headers(),
        json=_create_body(
            session_id,
            token=token,
            operation_id=resolved_operation_id,
            turn_id=turn_id,
            scope=scope,
        ),
    )
    return await _wait_for_terminal(
        client,
        response,
        operation_id=resolved_operation_id,
    )


async def _post_message_and_wait(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    token: int,
    operation_id: str,
    content: str,
    turn_id: str = "turn-1",
    scope: str = "tenant-1:customer-1",
) -> httpx.Response:
    response = await client.post(
        f"{FENCED_PREFIX}/sessions/{session_id}/messages",
        headers=_headers(),
        json=_message_body(
            token=token,
            operation_id=operation_id,
            content=content,
            turn_id=turn_id,
            scope=scope,
        ),
    )
    return await _wait_for_terminal(
        client,
        response,
        operation_id=operation_id,
    )


def _truncate_fencing_tables() -> None:
    import psycopg2

    with psycopg2.connect(_owner_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    openviking_fencing.commit_work_outbox,
                    openviking_fencing.effect_outbox,
                    openviking_fencing.session_turn_closure,
                    openviking_fencing.effect_receipt,
                    openviking_fencing.operation_receipt,
                    openviking_fencing.session_binding,
                    openviking_fencing.scope_state
                RESTART IDENTITY CASCADE
                """
            )


@pytest.fixture(autouse=True)
def fenced_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    if not fencing_database_url():
        pytest.skip("PostgreSQL fencing integration DSN is not configured")
    monkeypatch.setenv("OPENVIKING_ALICE_FENCING_MODE", "required")
    monkeypatch.setenv("OPENVIKING_ALICE_SERVICE_TOKEN", ALICE_TOKEN)
    # New submissions should expose the protocol's durable 202 boundary. Tests
    # that need a completed POST exercise exact duplicate submissions instead.
    monkeypatch.setenv("OPENVIKING_FENCED_POST_POLL_SECONDS", "0")


@pytest_asyncio.fixture(autouse=True)
async def fenced_runtime(
    fenced_environment: None,
    app,
) -> AsyncIterator[None]:
    del fenced_environment, app
    await fenced_sessions_routes.stop_fenced_writer_runtime()
    await validate_postgres_fencing_schema()
    await asyncio.to_thread(_truncate_fencing_tables)
    assert await fenced_sessions_routes.start_fenced_writer_runtime() is True
    try:
        yield
    finally:
        await fenced_sessions_routes.stop_fenced_writer_runtime()
        await asyncio.to_thread(_truncate_fencing_tables)


@pytest.mark.asyncio
async def test_health_advertises_configured_protocol_v2(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    capability = response.json()["capabilities"]["alice_session_fencing"]
    assert capability == {
        "protocol": "openviking-alice-session-fencing",
        "version": 2,
        "mode": "required",
        "write_ack": "202_poll",
        "configured": True,
        "writer_healthy": True,
        "draining": False,
        "effect_concurrency": 2,
        "commit_concurrency": 2,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["observe", "required"])
async def test_fenced_api_requires_strict_token_in_both_modes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("OPENVIKING_ALICE_FENCING_MODE", mode)
    session_id = _session_id(f"strict-token:{mode}")
    operation_id = _operation_id(f"strict-token:{mode}")
    body = _create_body(session_id, operation_id=operation_id)

    missing = await client.post(f"{FENCED_PREFIX}/sessions", json=body)
    wrong = await client.post(
        f"{FENCED_PREFIX}/sessions",
        headers=_headers("wrong-token"),
        json=body,
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHENTICATED"
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "UNAUTHENTICATED"
    health = await client.get("/health")
    capability = health.json()["capabilities"]["alice_session_fencing"]
    assert capability["mode"] == mode
    assert capability["configured"] is True

    accepted = await client.post(
        f"{FENCED_PREFIX}/sessions",
        headers=_headers(),
        json=body,
    )
    completed = await _wait_for_terminal(
        client,
        accepted,
        operation_id=operation_id,
    )
    assert _operation(completed)["result"]["session_id"] == session_id


@pytest.mark.asyncio
async def test_post_202_get_202_then_200_uses_nested_operation_envelope(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await fenced_sessions_routes.stop_fenced_writer_runtime()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_executor = fenced_sessions_routes.execute_fenced_outbox_item

    async def gated_executor(item):  # noqa: ANN001
        entered.set()
        await release.wait()
        return await original_executor(item)

    monkeypatch.setattr(
        fenced_sessions_routes,
        "execute_fenced_outbox_item",
        gated_executor,
    )
    assert await fenced_sessions_routes.start_fenced_writer_runtime() is True

    session_id = _session_id("202-poll-contract")
    operation_id = _operation_id("202-poll-contract")
    submitted = await client.post(
        f"{FENCED_PREFIX}/sessions",
        headers=_headers(),
        json=_create_body(session_id, operation_id=operation_id),
    )
    pending = _assert_pending(
        submitted,
        operation_id=operation_id,
        replayed=False,
    )

    await asyncio.wait_for(entered.wait(), timeout=2.0)
    running = await client.get(str(pending["status_url"]), headers=_headers())
    running_operation = _assert_pending(
        running,
        operation_id=operation_id,
        replayed=False,
    )
    assert running_operation["status"] == "running"

    release.set()
    completed = await _wait_for_terminal(
        client,
        running,
        operation_id=operation_id,
    )
    operation = _assert_completed(
        completed,
        operation_id=operation_id,
        replayed=False,
    )
    assert operation["result"]["session_id"] == session_id
    assert "session_id" not in completed.json()["result"]


@pytest.mark.asyncio
async def test_exact_replay_stale_fence_digest_and_scope_conflicts(
    client: httpx.AsyncClient,
) -> None:
    session_id = _session_id("replay-and-conflicts")
    scope = "tenant-replay:customer-replay"
    create_operation = _operation_id("replay-create")
    first_create = await _post_create_and_wait(
        client,
        session_id,
        operation_id=create_operation,
        scope=scope,
    )
    assert _operation(first_create)["result"]["session_id"] == session_id

    create_replay = await client.post(
        f"{FENCED_PREFIX}/sessions",
        headers=_headers(),
        json=_create_body(
            session_id,
            token=1,
            operation_id=create_operation,
            scope=scope,
        ),
    )
    create_replay_operation = _assert_completed(
        create_replay,
        operation_id=create_operation,
        replayed=True,
    )
    assert create_replay_operation["result"]["session_id"] == session_id

    message_operation = _operation_id("replay-message")
    first_message = await _post_message_and_wait(
        client,
        session_id,
        token=2,
        operation_id=message_operation,
        content="exactly once",
        scope=scope,
    )
    message_result = _operation(first_message)["result"]

    message_replay = await client.post(
        f"{FENCED_PREFIX}/sessions/{session_id}/messages",
        headers=_headers(),
        json=_message_body(
            token=2,
            operation_id=message_operation,
            content="exactly once",
            scope=scope,
        ),
    )
    replay_operation = _assert_completed(
        message_replay,
        operation_id=message_operation,
        replayed=True,
    )
    assert replay_operation["result"]["message_id"] == message_result["message_id"]

    digest_conflict = await client.post(
        f"{FENCED_PREFIX}/sessions/{session_id}/messages",
        headers=_headers(),
        json=_message_body(
            token=2,
            operation_id=message_operation,
            content="different content",
            scope=scope,
        ),
    )
    assert digest_conflict.status_code == 409
    assert digest_conflict.json()["error"]["details"]["reason"] == ("operation_digest_conflict")

    stale = await client.post(
        f"{FENCED_PREFIX}/sessions/{session_id}/messages",
        headers=_headers(),
        json=_message_body(
            token=1,
            operation_id=_operation_id("stale-message"),
            content="must be rejected",
            scope=scope,
        ),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {
        "reason": "stale_fence",
        "highest_fencing_token": 2,
        "received_fencing_token": 1,
    }

    foreign_scope = await client.post(
        f"{FENCED_PREFIX}/sessions/{session_id}/messages",
        headers=_headers(),
        json=_message_body(
            token=1,
            operation_id=_operation_id("foreign-scope-message"),
            content="must not cross scope",
            scope="tenant-replay:another-customer",
        ),
    )
    assert foreign_scope.status_code == 409
    assert foreign_scope.json()["error"]["details"]["reason"] == ("session_scope_conflict")


@pytest.mark.asyncio
async def test_writer_task_death_makes_readiness_fail_closed(
    client: httpx.AsyncClient,
) -> None:
    ready = await client.get("/ready")
    # Other embedded-test dependencies may be intentionally unavailable, so
    # establish the writer-specific healthy baseline instead of requiring every
    # unrelated readiness check to pass.
    assert ready.json()["checks"]["alice_fenced_writer"]["status"] == "ok"

    pool = fenced_sessions_routes._writer_pool
    assert pool is not None and pool.healthy
    failed_task = pool._tasks[0]
    failed_task.cancel()
    await asyncio.gather(failed_task, return_exceptions=True)
    await asyncio.sleep(0)

    not_ready = await client.get("/ready")
    assert not_ready.status_code == 503
    writer_check = not_ready.json()["checks"]["alice_fenced_writer"]
    assert writer_check == {
        "status": "error",
        "configured": False,
        "healthy": False,
        "draining": False,
    }
    health = await client.get("/health")
    capability = health.json()["capabilities"]["alice_session_fencing"]
    assert capability["configured"] is False
    assert capability["writer_healthy"] is False


@pytest.mark.asyncio
async def test_required_mode_rejects_legacy_alice_and_reserved_bypasses(
    client: httpx.AsyncClient,
) -> None:
    legacy_marker = await client.post(
        "/api/v1/sessions",
        headers={"X-OpenViking-Agent": "alice"},
        json={"session_id": "legacy-alice-required"},
    )
    authenticated_legacy = await client.post(
        "/api/v1/sessions",
        headers=_headers(),
        json={"session_id": "authenticated-legacy-alice-required"},
    )

    for response in (legacy_marker, authenticated_legacy):
        assert response.status_code == 412
        assert response.json()["error"]["details"]["reason"] == "fencing_required"

    reserved_id = _session_id("ordinary-reserved-required")
    reserved_create = await client.post(
        "/api/v1/sessions",
        json={"session_id": reserved_id},
    )
    reserved_message = await client.post(
        f"/api/v1/sessions/{reserved_id}/messages",
        json={"role": "user", "content": "bypass"},
    )
    for response in (reserved_create, reserved_message):
        assert response.status_code == 412
        assert response.json()["error"]["details"]["reason"] == ("alice_session_fencing_required")

    non_reserved_fenced = await client.post(
        f"{FENCED_PREFIX}/sessions",
        headers=_headers(),
        json=_create_body(
            "not-in-the-reserved-namespace",
            operation_id=_operation_id("non-reserved-fenced"),
        ),
    )
    assert non_reserved_fenced.status_code == 400


@pytest.mark.asyncio
async def test_observe_mode_keeps_ordinary_api_but_rejects_reserved_bypass(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENVIKING_ALICE_FENCING_MODE", "observe")

    ordinary = await client.post(
        "/api/v1/sessions",
        json={"session_id": "ordinary-observe"},
    )
    assert ordinary.status_code == 200

    reserved_id = _session_id("ordinary-reserved-observe")
    reserved = await client.post(
        "/api/v1/sessions",
        headers={"X-OpenViking-Agent": "alice"},
        json={"session_id": reserved_id},
    )
    assert reserved.status_code == 412
    assert reserved.json()["error"]["details"]["reason"] == ("alice_session_fencing_required")
