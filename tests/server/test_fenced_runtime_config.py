# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Fail-closed validation for fenced writer process deadlines."""

from __future__ import annotations

from typing import Any

import pytest

from openviking.server import fenced_postgres
from openviking.server.fenced_postgres import (
    FENCING_DATABASE_ROLE,
    _validate_runtime_fencing_principal,
    _validate_runtime_fencing_privilege_scope,
)
from openviking.server.routers import fenced_sessions as fenced_sessions_routes
from openviking.server.routers.fenced_sessions import _drain_timeout_seconds
from openviking.session.session import fenced_phase2_timeout_seconds

_SAFE_PRINCIPAL_ROW = (
    FENCING_DATABASE_ROLE,
    FENCING_DATABASE_ROLE,
    False,  # NOSUPERUSER
    False,  # NOCREATEDB
    False,  # NOCREATEROLE
    False,  # NOINHERIT
    False,  # NOREPLICATION
    False,  # NOBYPASSRLS
    True,  # LOGIN
    32,  # bounded connection limit
    0,  # no outgoing memberships
    True,  # CONNECT
    False,  # no CREATE DATABASE privilege
    False,  # no TEMPORARY privilege
    "pg_catalog",
)


class _ScriptedCursor:
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self._rows = list(rows)
        self.statements: list[tuple[str, Any]] = []

    def __enter__(self) -> _ScriptedCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> None:
        self.statements.append((query, params))

    def fetchone(self) -> tuple[Any, ...] | None:
        if not self._rows:
            raise AssertionError("unexpected cursor.fetchone()")
        return self._rows.pop(0)


class _ScriptedConnection:
    def __init__(self, cursor: _ScriptedCursor) -> None:
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self) -> _ScriptedCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_runtime_principal_accepts_only_dedicated_least_privilege_role() -> None:
    cursor = _ScriptedCursor([_SAFE_PRINCIPAL_ROW])

    _validate_runtime_fencing_principal(cursor)

    assert len(cursor.statements) == 1
    assert "current_user" in cursor.statements[0][0]
    assert "session_user" in cursor.statements[0][0]


@pytest.mark.parametrize(
    ("column", "unsafe_value", "error"),
    [
        (0, "postgres", "authenticate and connect"),
        (1, "postgres", "authenticate and connect"),
        (2, True, "unsafe attributes: SUPERUSER"),
        (3, True, "unsafe attributes: CREATEDB"),
        (4, True, "unsafe attributes: CREATEROLE"),
        (5, True, "unsafe attributes: INHERIT"),
        (6, True, "unsafe attributes: REPLICATION"),
        (7, True, "unsafe attributes: BYPASSRLS"),
        (8, False, "unsafe attributes: NOLOGIN"),
        (9, 33, "unsafe attributes: CONNECTION LIMIT"),
        (10, 1, "must not be a member"),
        (11, False, "unsafe database privileges"),
        (12, True, "unsafe database privileges"),
        (13, True, "unsafe database privileges"),
        (14, '"$user", public', "search_path=pg_catalog"),
    ],
)
def test_runtime_principal_rejects_identity_attribute_and_database_drift(
    column: int,
    unsafe_value: Any,
    error: str,
) -> None:
    row = list(_SAFE_PRINCIPAL_ROW)
    row[column] = unsafe_value
    cursor = _ScriptedCursor([tuple(row)])

    with pytest.raises(RuntimeError, match=error):
        _validate_runtime_fencing_principal(cursor)


def _safe_privilege_rows() -> list[tuple[Any, ...]]:
    return [
        (True, False, False, False, False),
        *((True, True, True, True, False, False, False),) * 7,
        *((True, True, False),) * 2,
    ]


def test_runtime_privilege_scope_accepts_exact_fencing_grants() -> None:
    cursor = _ScriptedCursor(_safe_privilege_rows())

    _validate_runtime_fencing_privilege_scope(cursor)

    assert len(cursor.statements) == 10


@pytest.mark.parametrize(
    ("scope_row", "error"),
    [
        ((False, False, False, False, False), "unsafe schema privileges"),
        ((True, True, False, False, False), "unsafe schema privileges"),
        ((True, False, True, False, False), "create in a non-system schema"),
        ((True, False, False, True, False), "unexpected table"),
        ((True, False, False, False, True), "unexpected sequence"),
    ],
)
def test_runtime_privilege_scope_rejects_effective_privilege_drift(
    scope_row: tuple[bool, bool, bool, bool, bool],
    error: str,
) -> None:
    cursor = _ScriptedCursor([scope_row])

    with pytest.raises(RuntimeError, match=error):
        _validate_runtime_fencing_privilege_scope(cursor)


def test_runtime_privilege_scope_rejects_extra_table_or_sequence_grants() -> None:
    unsafe_table_rows = _safe_privilege_rows()
    unsafe_table_rows[1] = (True, True, True, True, True, False, False)
    with pytest.raises(RuntimeError, match="invalid table privileges"):
        _validate_runtime_fencing_privilege_scope(_ScriptedCursor(unsafe_table_rows))

    unsafe_sequence_rows = _safe_privilege_rows()
    unsafe_sequence_rows[-1] = (True, True, True)
    with pytest.raises(RuntimeError, match="invalid sequence privileges"):
        _validate_runtime_fencing_privilege_scope(_ScriptedCursor(unsafe_sequence_rows))


@pytest.mark.asyncio
async def test_schema_validator_rejects_owner_before_schema_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_row = (
        "postgres",
        "postgres",
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        -1,
        0,
        True,
        True,
        True,
        '"$user", public',
    )
    cursor = _ScriptedCursor([owner_row])
    connection = _ScriptedConnection(cursor)
    monkeypatch.setattr(fenced_postgres, "_connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="authenticate and connect"):
        await fenced_postgres.validate_postgres_fencing_schema()

    assert connection.closed is True
    assert len(cursor.statements) == 1
    assert "pg_catalog.pg_roles" in cursor.statements[0][0]


@pytest.mark.asyncio
async def test_required_writer_startup_rejects_owner_or_superuser_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_row = (
        "postgres",
        "postgres",
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        -1,
        0,
        True,
        True,
        True,
        '"$user", public',
    )
    connection = _ScriptedConnection(_ScriptedCursor([owner_row]))
    monkeypatch.setenv("OPENVIKING_ALICE_FENCING_MODE", "required")
    monkeypatch.setenv(
        "OPENVIKING_ALICE_FENCING_DATABASE_URL",
        "postgresql://postgres:owner-secret@db/fencing",
    )
    monkeypatch.setenv(
        "OPENVIKING_ALICE_SERVICE_TOKEN",
        "test-openviking-alice-service-token-0001",
    )
    monkeypatch.setattr(fenced_postgres, "_connect", lambda **_kwargs: connection)
    monkeypatch.setattr(fenced_sessions_routes, "_writer_pool", None)
    monkeypatch.setattr(fenced_sessions_routes, "_writer_schema_validated", False)
    monkeypatch.setattr(fenced_sessions_routes, "_writer_draining", False)

    with pytest.raises(RuntimeError, match="authenticate and connect"):
        await fenced_sessions_routes.start_fenced_writer_runtime()

    assert connection.closed is True
    assert fenced_sessions_routes.fenced_writer_runtime_status()["configured"] is False


def test_fenced_runtime_timeout_defaults_are_coherent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENVIKING_FENCED_PHASE2_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OPENVIKING_FENCED_DRAIN_TIMEOUT_SECONDS", raising=False)
    phase2 = fenced_phase2_timeout_seconds()
    assert phase2 == 1800.0
    assert _drain_timeout_seconds(phase2) == 1860.0


@pytest.mark.parametrize("value", ["", "bad", "nan", "inf", "59", "7201"])
def test_fenced_phase2_timeout_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENVIKING_FENCED_PHASE2_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="FENCED_PHASE2_TIMEOUT_SECONDS"):
        fenced_phase2_timeout_seconds()


@pytest.mark.parametrize("value", ["", "bad", "nan", "inf", "1859", "10801"])
def test_fenced_drain_timeout_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENVIKING_FENCED_DRAIN_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="FENCED_DRAIN_TIMEOUT_SECONDS"):
        _drain_timeout_seconds(1800.0)
