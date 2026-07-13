# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL authority and durable outbox for Alice fenced session effects.

AGFS path locks are renewable leases and AGFS writes are unconditional.  They
therefore cannot be the authority for an external fencing token: a process that
is frozen beyond the lease TTL may resume after a takeover and overwrite newer
state.  Required mode accepts an operation into PostgreSQL in a short atomic
boundary.  Alice/API request owners never execute the AGFS effect; one durable
outbox writer applies accepted effects in sequence.

The tables are intentionally created by deployment migrations, never by the
runtime request or startup path.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Optional

from openviking.server.fenced_operation import (
    FencedOperationConflict,
    FencedOperationEnvelope,
    operation_digest,
)
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import FailedPreconditionError, UnavailableError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

FENCING_DATABASE_URL_ENV = "OPENVIKING_ALICE_FENCING_DATABASE_URL"
FENCING_SERVICE_TOKEN_ENV = "OPENVIKING_ALICE_SERVICE_TOKEN"
FENCING_SUBMIT_IDLE_TIMEOUT_MS_ENV = "OPENVIKING_ALICE_FENCING_SUBMIT_IDLE_TIMEOUT_MS"
FENCING_DATABASE_ROLE = "openviking_fencing"


def _record_v2_submit_metric(
    operation: str,
    outcome: str,
    started_at: float,
) -> None:
    try:
        from openviking.metrics.datasources.session import (  # noqa: PLC0415
            SessionLifecycleDataSource,
        )

        SessionLifecycleDataSource.record_fencing(
            operation=operation,
            outcome=outcome,
            latency_seconds=max(0.0, time.monotonic() - started_at),
        )
    except Exception:
        logger.debug("Failed to record v2 fencing submit metric", exc_info=True)


def _record_suppressed_effect_metrics(operations: list[str]) -> None:
    if not operations:
        return
    try:
        from openviking.metrics.datasources.session import (  # noqa: PLC0415
            SessionLifecycleDataSource,
        )

        for operation in operations:
            SessionLifecycleDataSource.record_fenced_effect(
                operation=operation,
                outcome="suppressed",
            )
    except Exception:
        logger.debug("Failed to record suppressed fenced effects", exc_info=True)


SCHEMA = "openviking_fencing"

# Kept here as the canonical contract for the root deployment migration.
POSTGRES_FENCING_DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

CREATE TABLE IF NOT EXISTS {SCHEMA}.scope_state (
    account_id text NOT NULL,
    user_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    highest_fencing_token bigint NOT NULL,
    active_turn_id text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, user_id, writer, session_scope_id)
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.session_binding (
    account_id text NOT NULL,
    user_id text NOT NULL,
    session_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, user_id, session_id)
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.operation_receipt (
    account_id text NOT NULL,
    user_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    operation_id text NOT NULL,
    operation text NOT NULL,
    resource_id text NOT NULL,
    turn_id text NOT NULL,
    digest text NOT NULL,
    fencing_token bigint NOT NULL,
    state text NOT NULL,
    result jsonb,
    error jsonb,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, user_id, writer, session_scope_id, operation_id)
);

ALTER TABLE {SCHEMA}.operation_receipt
    ADD COLUMN IF NOT EXISTS error jsonb;
ALTER TABLE {SCHEMA}.operation_receipt
    ADD COLUMN IF NOT EXISTS submitted_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE {SCHEMA}.operation_receipt
    DROP CONSTRAINT IF EXISTS operation_receipt_state_check;
UPDATE {SCHEMA}.operation_receipt SET state='queued' WHERE state='prepared';
UPDATE {SCHEMA}.operation_receipt SET state='completed' WHERE state='done';
ALTER TABLE {SCHEMA}.operation_receipt
    ADD CONSTRAINT operation_receipt_state_check
    CHECK (state IN ('queued', 'running', 'completed', 'stale', 'failed', 'conflict'));

CREATE INDEX IF NOT EXISTS operation_receipt_fence_idx
ON {SCHEMA}.operation_receipt
    (account_id, user_id, writer, session_scope_id, fencing_token);

CREATE UNIQUE INDEX IF NOT EXISTS operation_receipt_principal_operation_uq
ON {SCHEMA}.operation_receipt (account_id, user_id, writer, operation_id);

CREATE TABLE IF NOT EXISTS {SCHEMA}.effect_receipt (
    account_id text NOT NULL,
    user_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    operation_id text NOT NULL,
    operation text NOT NULL,
    resource_id text NOT NULL,
    turn_id text NOT NULL,
    digest text NOT NULL,
    fencing_token bigint NOT NULL,
    result jsonb NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, user_id, writer, session_scope_id, operation_id)
);

CREATE INDEX IF NOT EXISTS effect_receipt_fence_idx
ON {SCHEMA}.effect_receipt
    (account_id, user_id, writer, session_scope_id, fencing_token);

CREATE TABLE IF NOT EXISTS {SCHEMA}.effect_outbox (
    sequence_id bigserial NOT NULL UNIQUE,
    account_id text NOT NULL,
    user_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    operation_id text NOT NULL,
    operation text NOT NULL,
    resource_id text NOT NULL,
    turn_id text NOT NULL,
    digest text NOT NULL,
    fencing_token bigint NOT NULL,
    request_payload jsonb NOT NULL,
    actor_peer_id text,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running')),
    attempt_count integer NOT NULL DEFAULT 0,
    claim_token text,
    available_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    effect_started_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, user_id, writer, session_scope_id, operation_id)
);

ALTER TABLE {SCHEMA}.effect_outbox
    ADD COLUMN IF NOT EXISTS effect_started_at timestamptz;
ALTER TABLE {SCHEMA}.effect_outbox
    ADD COLUMN IF NOT EXISTS claim_token text;
ALTER TABLE {SCHEMA}.effect_outbox
    DROP COLUMN IF EXISTS effect_result;
ALTER TABLE {SCHEMA}.effect_outbox
    DROP COLUMN IF EXISTS wait_task_id;
ALTER TABLE {SCHEMA}.effect_outbox
    DROP CONSTRAINT IF EXISTS effect_outbox_state_check;
DELETE FROM {SCHEMA}.effect_outbox WHERE state NOT IN ('queued', 'running');
ALTER TABLE {SCHEMA}.effect_outbox
    ADD CONSTRAINT effect_outbox_state_check
    CHECK (state IN ('queued', 'running'));

CREATE INDEX IF NOT EXISTS effect_outbox_ready_idx
ON {SCHEMA}.effect_outbox (state, available_at, sequence_id);

CREATE INDEX IF NOT EXISTS effect_outbox_scope_fence_idx
ON {SCHEMA}.effect_outbox
    (account_id, user_id, writer, session_scope_id, fencing_token);

CREATE UNIQUE INDEX IF NOT EXISTS effect_outbox_principal_operation_uq
ON {SCHEMA}.effect_outbox (account_id, user_id, writer, operation_id);

CREATE TABLE IF NOT EXISTS {SCHEMA}.commit_work_outbox (
    sequence_id bigserial NOT NULL UNIQUE,
    account_id text NOT NULL,
    user_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    operation_id text NOT NULL,
    session_id text NOT NULL,
    task_id text NOT NULL,
    archive_uri text NOT NULL,
    wait_for_completion boolean NOT NULL DEFAULT false,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'running', 'ambiguous')),
    attempt_count integer NOT NULL DEFAULT 0,
    claim_token text,
    error jsonb,
    available_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id,user_id,writer,session_scope_id,operation_id),
    UNIQUE (account_id,user_id,writer,task_id)
);

ALTER TABLE {SCHEMA}.commit_work_outbox
    ADD COLUMN IF NOT EXISTS sequence_id bigserial;
ALTER TABLE {SCHEMA}.commit_work_outbox
    ADD COLUMN IF NOT EXISTS wait_for_completion boolean NOT NULL DEFAULT false;
ALTER TABLE {SCHEMA}.commit_work_outbox
    ADD COLUMN IF NOT EXISTS error jsonb;
ALTER TABLE {SCHEMA}.commit_work_outbox
    DROP CONSTRAINT IF EXISTS commit_work_outbox_state_check;
ALTER TABLE {SCHEMA}.commit_work_outbox
    ADD CONSTRAINT commit_work_outbox_state_check
    CHECK (state IN ('pending', 'running', 'ambiguous'));

CREATE UNIQUE INDEX IF NOT EXISTS commit_work_outbox_sequence_uq
ON {SCHEMA}.commit_work_outbox (sequence_id);

CREATE UNIQUE INDEX IF NOT EXISTS commit_work_outbox_principal_task_uq
ON {SCHEMA}.commit_work_outbox (account_id,user_id,writer,task_id);

CREATE INDEX IF NOT EXISTS commit_work_outbox_ready_idx
ON {SCHEMA}.commit_work_outbox (state, available_at, sequence_id);

CREATE TABLE IF NOT EXISTS {SCHEMA}.session_turn_closure (
    account_id text NOT NULL,
    user_id text NOT NULL,
    writer text NOT NULL,
    session_scope_id text NOT NULL,
    turn_id text NOT NULL,
    session_id text NOT NULL,
    operation_id text NOT NULL,
    digest text NOT NULL,
    fencing_token bigint NOT NULL,
    result jsonb NOT NULL,
    closed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (
        account_id, user_id, writer, session_scope_id, turn_id, session_id
    )
);

ALTER TABLE {SCHEMA}.scope_state
    DROP CONSTRAINT IF EXISTS scope_state_writer_check;
ALTER TABLE {SCHEMA}.scope_state
    ADD CONSTRAINT scope_state_writer_check CHECK (writer = 'alice');
ALTER TABLE {SCHEMA}.session_binding
    DROP CONSTRAINT IF EXISTS session_binding_writer_check;
ALTER TABLE {SCHEMA}.session_binding
    ADD CONSTRAINT session_binding_writer_check CHECK (writer = 'alice');
ALTER TABLE {SCHEMA}.operation_receipt
    DROP CONSTRAINT IF EXISTS operation_receipt_writer_check;
ALTER TABLE {SCHEMA}.operation_receipt
    ADD CONSTRAINT operation_receipt_writer_check CHECK (writer = 'alice');
ALTER TABLE {SCHEMA}.effect_receipt
    DROP CONSTRAINT IF EXISTS effect_receipt_writer_check;
ALTER TABLE {SCHEMA}.effect_receipt
    ADD CONSTRAINT effect_receipt_writer_check CHECK (writer = 'alice');
ALTER TABLE {SCHEMA}.effect_outbox
    DROP CONSTRAINT IF EXISTS effect_outbox_writer_check;
ALTER TABLE {SCHEMA}.effect_outbox
    ADD CONSTRAINT effect_outbox_writer_check CHECK (writer = 'alice');
ALTER TABLE {SCHEMA}.commit_work_outbox
    DROP CONSTRAINT IF EXISTS commit_work_outbox_writer_check;
ALTER TABLE {SCHEMA}.commit_work_outbox
    ADD CONSTRAINT commit_work_outbox_writer_check CHECK (writer = 'alice');
ALTER TABLE {SCHEMA}.session_turn_closure
    DROP CONSTRAINT IF EXISTS session_turn_closure_writer_check;
ALTER TABLE {SCHEMA}.session_turn_closure
    ADD CONSTRAINT session_turn_closure_writer_check CHECK (writer = 'alice');
"""

_REQUIRED_TABLES = (
    "scope_state",
    "session_binding",
    "operation_receipt",
    "effect_receipt",
    "effect_outbox",
    "commit_work_outbox",
    "session_turn_closure",
)

_REQUIRED_SEQUENCES = (
    "effect_outbox_sequence_id_seq",
    "commit_work_outbox_sequence_id_seq",
)

_REQUIRED_COLUMNS = {
    "scope_state": {
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "highest_fencing_token",
        "active_turn_id",
        "updated_at",
    },
    "session_binding": {
        "account_id",
        "user_id",
        "session_id",
        "writer",
        "session_scope_id",
        "created_at",
    },
    "operation_receipt": {
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
        "operation",
        "resource_id",
        "turn_id",
        "digest",
        "fencing_token",
        "state",
        "result",
        "error",
        "submitted_at",
        "updated_at",
    },
    "effect_outbox": {
        "sequence_id",
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
        "operation",
        "resource_id",
        "turn_id",
        "digest",
        "fencing_token",
        "request_payload",
        "actor_peer_id",
        "state",
        "attempt_count",
        "claim_token",
        "available_at",
        "claimed_at",
        "effect_started_at",
        "updated_at",
    },
    "effect_receipt": {
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
        "operation",
        "resource_id",
        "turn_id",
        "digest",
        "fencing_token",
        "result",
        "completed_at",
    },
    "commit_work_outbox": {
        "sequence_id",
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
        "session_id",
        "task_id",
        "archive_uri",
        "wait_for_completion",
        "state",
        "attempt_count",
        "claim_token",
        "error",
        "available_at",
        "started_at",
        "created_at",
        "updated_at",
    },
    "session_turn_closure": {
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "turn_id",
        "session_id",
        "operation_id",
        "digest",
        "fencing_token",
        "result",
        "closed_at",
    },
}

_REQUIRED_PRIMARY_KEYS = {
    "scope_state": ("account_id", "user_id", "writer", "session_scope_id"),
    "session_binding": ("account_id", "user_id", "session_id"),
    "operation_receipt": (
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
    ),
    "effect_outbox": (
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
    ),
    "effect_receipt": (
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
    ),
    "commit_work_outbox": (
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "operation_id",
    ),
    "session_turn_closure": (
        "account_id",
        "user_id",
        "writer",
        "session_scope_id",
        "turn_id",
        "session_id",
    ),
}

_REQUIRED_UNIQUE_INDEXES = {
    "operation_receipt_principal_operation_uq": (
        "account_id",
        "user_id",
        "writer",
        "operation_id",
    ),
    "effect_outbox_principal_operation_uq": (
        "account_id",
        "user_id",
        "writer",
        "operation_id",
    ),
}

_REQUIRED_UNIQUE_COLUMN_SETS = {
    "effect_outbox": (("sequence_id",),),
    "commit_work_outbox": (
        ("sequence_id",),
        ("account_id", "user_id", "writer", "task_id"),
    ),
}

_REQUIRED_STATE_CONSTRAINTS = {
    "operation_receipt_state_check": {
        "queued",
        "running",
        "completed",
        "stale",
        "failed",
        "conflict",
    },
    "effect_outbox_state_check": {"queued", "running"},
    "commit_work_outbox_state_check": {"pending", "running", "ambiguous"},
}

_REQUIRED_WRITER_CONSTRAINTS = {
    "scope_state_writer_check",
    "session_binding_writer_check",
    "operation_receipt_writer_check",
    "effect_receipt_writer_check",
    "effect_outbox_writer_check",
    "commit_work_outbox_writer_check",
    "session_turn_closure_writer_check",
}


def fencing_database_url() -> str:
    return os.getenv(FENCING_DATABASE_URL_ENV, "").strip()


def fencing_service_token() -> str:
    return os.getenv(FENCING_SERVICE_TOKEN_ENV, "").strip()


def is_valid_alice_service_token(provided: Optional[str]) -> bool:
    """Authenticate the dedicated Alice principal without trusting identity headers."""
    expected = fencing_service_token()
    if not expected or not isinstance(provided, str):
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def validate_required_fencing_configuration() -> None:
    """Fail synchronously before the server starts accepting traffic."""
    _submit_idle_timeout_ms()
    missing = []
    if not fencing_database_url():
        missing.append(FENCING_DATABASE_URL_ENV)
    token = fencing_service_token()
    if not token:
        missing.append(FENCING_SERVICE_TOKEN_ENV)
    elif len(token.encode("utf-8")) < 32:
        raise RuntimeError(f"{FENCING_SERVICE_TOKEN_ENV} must be at least 32 bytes")
    if missing:
        raise RuntimeError("required Alice fencing is missing: " + ", ".join(missing))
    try:
        import psycopg2  # noqa: F401, PLC0415
    except ImportError as exc:  # pragma: no cover - packaging/startup guard
        raise RuntimeError(
            "required Alice fencing needs the 'alice-fencing' package extra (psycopg2-binary)"
        ) from exc


def _submit_idle_timeout_ms() -> int:
    raw = os.getenv(FENCING_SUBMIT_IDLE_TIMEOUT_MS_ENV, "2000").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{FENCING_SUBMIT_IDLE_TIMEOUT_MS_ENV} must be an integer") from exc
    if value < 500 or value > 30_000:
        raise RuntimeError(f"{FENCING_SUBMIT_IDLE_TIMEOUT_MS_ENV} must be between 500 and 30000")
    return value


def _connect(*, application_name: str = "openviking-alice-fencing"):
    import psycopg2  # type: ignore  # noqa: PLC0415

    timeout_ms = _submit_idle_timeout_ms()
    return psycopg2.connect(
        fencing_database_url(),
        connect_timeout=5,
        application_name=application_name,
        # A client frozen inside the short submit transaction cannot retain a
        # scope/resource advisory-xact lock forever.  This is a DB-side timeout:
        # SIGSTOP also stops the Python process, but PostgreSQL still rolls the
        # idle transaction back and lets a higher fence submit.
        options=(
            f"-c idle_in_transaction_session_timeout={timeout_ms} "
            f"-c lock_timeout={max(timeout_ms * 2, 1000)}"
        ),
    )


def _validate_runtime_fencing_principal(cursor: Any) -> None:
    """Reject a connection that has more authority than the fencing writer needs."""

    cursor.execute(
        """
        SELECT current_user,
               session_user,
               runtime_role.rolsuper,
               runtime_role.rolcreatedb,
               runtime_role.rolcreaterole,
               runtime_role.rolinherit,
               runtime_role.rolreplication,
               runtime_role.rolbypassrls,
               runtime_role.rolcanlogin,
               runtime_role.rolconnlimit,
               (
                   SELECT count(*)
                   FROM pg_catalog.pg_auth_members AS membership
                   WHERE membership.member = runtime_role.oid
               ) AS membership_count,
               pg_catalog.has_database_privilege(
                   current_user,
                   pg_catalog.current_database(),
                   'CONNECT'
               ) AS can_connect,
               pg_catalog.has_database_privilege(
                   current_user,
                   pg_catalog.current_database(),
                   'CREATE'
               ) AS can_create,
               pg_catalog.has_database_privilege(
                   current_user,
                   pg_catalog.current_database(),
                   'TEMPORARY'
               ) AS can_temporary,
               pg_catalog.current_setting('search_path') AS search_path
        FROM pg_catalog.pg_roles AS runtime_role
        WHERE runtime_role.rolname = current_user
        """
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("unable to verify the PostgreSQL fencing runtime principal")

    (
        current_role,
        authenticated_role,
        is_superuser,
        can_create_database,
        can_create_role,
        inherits_roles,
        can_replicate,
        can_bypass_rls,
        can_login,
        connection_limit,
        membership_count,
        can_connect,
        can_create,
        can_temporary,
        search_path,
    ) = row
    if current_role != FENCING_DATABASE_ROLE or authenticated_role != FENCING_DATABASE_ROLE:
        raise RuntimeError(
            f"PostgreSQL fencing runtime must authenticate and connect as {FENCING_DATABASE_ROLE}"
        )

    unsafe_attributes = []
    if is_superuser:
        unsafe_attributes.append("SUPERUSER")
    if can_create_database:
        unsafe_attributes.append("CREATEDB")
    if can_create_role:
        unsafe_attributes.append("CREATEROLE")
    if inherits_roles:
        unsafe_attributes.append("INHERIT")
    if can_replicate:
        unsafe_attributes.append("REPLICATION")
    if can_bypass_rls:
        unsafe_attributes.append("BYPASSRLS")
    if not can_login:
        unsafe_attributes.append("NOLOGIN")
    if connection_limit != 32:
        unsafe_attributes.append("CONNECTION LIMIT")
    if unsafe_attributes:
        raise RuntimeError(
            "PostgreSQL fencing runtime role has unsafe attributes: " + ", ".join(unsafe_attributes)
        )
    if membership_count != 0:
        raise RuntimeError("PostgreSQL fencing runtime role must not be a member of another role")
    if can_connect is not True or can_create is not False or can_temporary is not False:
        raise RuntimeError("PostgreSQL fencing runtime role has unsafe database privileges")
    if search_path != "pg_catalog":
        raise RuntimeError("PostgreSQL fencing runtime role must use search_path=pg_catalog")


def _validate_runtime_fencing_privilege_scope(cursor: Any) -> None:
    """Detect effective privilege drift after the migrator provisions the role."""

    cursor.execute(
        """
        WITH non_system_relations AS MATERIALIZED (
            SELECT relation.oid,
                   relation.relkind,
                   relation.relname,
                   namespace.nspname
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname <> 'information_schema'
              AND namespace.nspname !~ '^pg_'
        ),
        unexpected_tables AS MATERIALIZED (
            SELECT oid
            FROM non_system_relations
            WHERE relkind IN ('r', 'p', 'v', 'm', 'f')
              AND NOT (nspname = %s AND relname = ANY(%s))
        ),
        unexpected_sequences AS MATERIALIZED (
            SELECT oid
            FROM non_system_relations
            WHERE relkind = 'S'
              AND NOT (nspname = %s AND relname = ANY(%s))
        )
        SELECT pg_catalog.has_schema_privilege(
                   current_user, %s, 'USAGE'
               ) AS can_use_fencing_schema,
               pg_catalog.has_schema_privilege(
                   current_user, %s, 'CREATE'
               ) AS can_create_in_fencing_schema,
               EXISTS (
                   SELECT 1
                   FROM pg_catalog.pg_namespace AS namespace
                   WHERE namespace.nspname <> 'information_schema'
                     AND namespace.nspname !~ '^pg_'
                     AND pg_catalog.has_schema_privilege(
                         current_user, namespace.oid, 'CREATE'
                     )
               ) AS can_create_in_non_system_schema,
               EXISTS (
                   SELECT 1
                   FROM unexpected_tables
                   WHERE pg_catalog.has_table_privilege(
                         current_user,
                         unexpected_tables.oid,
                         'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                     )
               ) AS can_access_unexpected_table,
               EXISTS (
                   SELECT 1
                   FROM unexpected_sequences
                   WHERE pg_catalog.has_sequence_privilege(
                         current_user,
                         unexpected_sequences.oid,
                         'USAGE,SELECT,UPDATE'
                     )
               ) AS can_access_unexpected_sequence
        """,
        (
            SCHEMA,
            list(_REQUIRED_TABLES),
            SCHEMA,
            list(_REQUIRED_SEQUENCES),
            SCHEMA,
            SCHEMA,
        ),
    )
    scope_row = cursor.fetchone()
    if not scope_row or scope_row[0] is not True or scope_row[1] is not False:
        raise RuntimeError("PostgreSQL fencing runtime role has unsafe schema privileges")
    if scope_row[2] is not False:
        raise RuntimeError("PostgreSQL fencing runtime role can create in a non-system schema")
    if scope_row[3] is not False:
        raise RuntimeError("PostgreSQL fencing runtime role can access an unexpected table")
    if scope_row[4] is not False:
        raise RuntimeError("PostgreSQL fencing runtime role can access an unexpected sequence")

    for table in _REQUIRED_TABLES:
        cursor.execute(
            """
            SELECT pg_catalog.has_table_privilege(current_user, %s, 'SELECT'),
                   pg_catalog.has_table_privilege(current_user, %s, 'INSERT'),
                   pg_catalog.has_table_privilege(current_user, %s, 'UPDATE'),
                   pg_catalog.has_table_privilege(current_user, %s, 'DELETE'),
                   pg_catalog.has_table_privilege(current_user, %s, 'TRUNCATE'),
                   pg_catalog.has_table_privilege(current_user, %s, 'REFERENCES'),
                   pg_catalog.has_table_privilege(current_user, %s, 'TRIGGER')
            """,
            (f"{SCHEMA}.{table}",) * 7,
        )
        table_row = cursor.fetchone()
        if not table_row or tuple(table_row) != (
            True,
            True,
            True,
            True,
            False,
            False,
            False,
        ):
            raise RuntimeError(
                f"PostgreSQL fencing runtime role has invalid table privileges on {SCHEMA}.{table}"
            )

    for sequence in _REQUIRED_SEQUENCES:
        cursor.execute(
            """
            SELECT pg_catalog.has_sequence_privilege(current_user, %s, 'USAGE'),
                   pg_catalog.has_sequence_privilege(current_user, %s, 'SELECT'),
                   pg_catalog.has_sequence_privilege(current_user, %s, 'UPDATE')
            """,
            (f"{SCHEMA}.{sequence}",) * 3,
        )
        sequence_row = cursor.fetchone()
        if not sequence_row or tuple(sequence_row) != (True, True, False):
            raise RuntimeError(
                "PostgreSQL fencing runtime role has invalid sequence privileges on "
                f"{SCHEMA}.{sequence}"
            )


async def validate_postgres_fencing_schema() -> None:
    """Verify DB reachability and migration presence during application lifespan."""

    def _validate() -> None:
        conn = _connect()
        try:
            conn.autocommit = True
            with conn.cursor() as cursor:
                # Validate effective and authenticated identities before any
                # schema inspection. A leaked owner/superuser DSN must never
                # be accepted by the application runtime.
                _validate_runtime_fencing_principal(cursor)
                for table in _REQUIRED_TABLES:
                    cursor.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.{table}",))
                    row = cursor.fetchone()
                    if not row or row[0] is None:
                        raise RuntimeError(
                            f"missing PostgreSQL fencing migration: {SCHEMA}.{table}"
                        )
                    cursor.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema=%s AND table_name=%s
                        """,
                        (SCHEMA, table),
                    )
                    actual_columns = {str(value[0]) for value in cursor.fetchall()}
                    missing_columns = _REQUIRED_COLUMNS[table] - actual_columns
                    if missing_columns:
                        raise RuntimeError(
                            "incomplete PostgreSQL fencing migration: "
                            f"{SCHEMA}.{table} missing columns "
                            + ", ".join(sorted(missing_columns))
                        )

                for index_name, expected_columns in _REQUIRED_UNIQUE_INDEXES.items():
                    cursor.execute(
                        """
                        SELECT i.indisunique,
                               array_agg(a.attname ORDER BY keys.ordinality)
                        FROM pg_catalog.pg_index i
                        JOIN pg_catalog.pg_class idx ON idx.oid=i.indexrelid
                        JOIN pg_catalog.pg_namespace ns ON ns.oid=idx.relnamespace
                        JOIN LATERAL unnest(i.indkey)
                            WITH ORDINALITY AS keys(attnum, ordinality) ON true
                        JOIN pg_catalog.pg_class tbl ON tbl.oid=i.indrelid
                        JOIN pg_catalog.pg_attribute a
                          ON a.attrelid=tbl.oid AND a.attnum=keys.attnum
                        WHERE ns.nspname=%s AND idx.relname=%s
                        GROUP BY i.indisunique
                        """,
                        (SCHEMA, index_name),
                    )
                    index_row = cursor.fetchone()
                    if (
                        index_row is None
                        or index_row[0] is not True
                        or tuple(index_row[1]) != expected_columns
                    ):
                        raise RuntimeError(
                            "incomplete PostgreSQL fencing migration: invalid unique "
                            f"index {SCHEMA}.{index_name}"
                        )

                for table, expected_columns in _REQUIRED_PRIMARY_KEYS.items():
                    cursor.execute(
                        """
                        SELECT array_agg(a.attname ORDER BY keys.ordinality)
                        FROM pg_catalog.pg_constraint c
                        JOIN LATERAL unnest(c.conkey)
                            WITH ORDINALITY AS keys(attnum, ordinality) ON true
                        JOIN pg_catalog.pg_attribute a
                          ON a.attrelid=c.conrelid AND a.attnum=keys.attnum
                        WHERE c.conrelid=%s::regclass AND c.contype='p'
                        """,
                        (f"{SCHEMA}.{table}",),
                    )
                    primary_key_row = cursor.fetchone()
                    actual_columns = (
                        tuple(primary_key_row[0]) if primary_key_row and primary_key_row[0] else ()
                    )
                    if actual_columns != expected_columns:
                        raise RuntimeError(
                            "incomplete PostgreSQL fencing migration: invalid primary "
                            f"key on {SCHEMA}.{table}"
                        )

                for table, required_sets in _REQUIRED_UNIQUE_COLUMN_SETS.items():
                    cursor.execute(
                        """
                        SELECT array_agg(a.attname ORDER BY keys.ordinality)
                        FROM pg_catalog.pg_index i
                        JOIN pg_catalog.pg_class tbl ON tbl.oid=i.indrelid
                        JOIN pg_catalog.pg_namespace ns ON ns.oid=tbl.relnamespace
                        JOIN LATERAL unnest(i.indkey)
                            WITH ORDINALITY AS keys(attnum, ordinality) ON true
                        JOIN pg_catalog.pg_attribute a
                          ON a.attrelid=tbl.oid AND a.attnum=keys.attnum
                        WHERE ns.nspname=%s AND tbl.relname=%s AND i.indisunique
                        GROUP BY i.indexrelid
                        """,
                        (SCHEMA, table),
                    )
                    actual_sets = {tuple(row[0]) for row in cursor.fetchall() if row and row[0]}
                    for required_columns in required_sets:
                        if required_columns not in actual_sets:
                            raise RuntimeError(
                                "incomplete PostgreSQL fencing migration: missing "
                                f"unique key on {SCHEMA}.{table}"
                            )

                for constraint_name, expected_states in _REQUIRED_STATE_CONSTRAINTS.items():
                    cursor.execute(
                        """
                        SELECT pg_get_constraintdef(c.oid)
                        FROM pg_catalog.pg_constraint c
                        JOIN pg_catalog.pg_namespace ns ON ns.oid=c.connamespace
                        WHERE ns.nspname=%s AND c.conname=%s AND c.contype='c'
                        """,
                        (SCHEMA, constraint_name),
                    )
                    constraint_row = cursor.fetchone()
                    definition = str(constraint_row[0]) if constraint_row else ""
                    actual_states = set(re.findall(r"'([^']+)'", definition))
                    if actual_states != expected_states:
                        raise RuntimeError(
                            "incomplete PostgreSQL fencing migration: invalid state "
                            f"constraint {SCHEMA}.{constraint_name}"
                        )

                for constraint_name in _REQUIRED_WRITER_CONSTRAINTS:
                    cursor.execute(
                        """
                        SELECT pg_get_constraintdef(c.oid)
                        FROM pg_catalog.pg_constraint c
                        JOIN pg_catalog.pg_namespace ns ON ns.oid=c.connamespace
                        WHERE ns.nspname=%s AND c.conname=%s AND c.contype='c'
                        """,
                        (SCHEMA, constraint_name),
                    )
                    constraint_row = cursor.fetchone()
                    definition = str(constraint_row[0]).lower() if constraint_row else ""
                    if "writer" not in definition or "'alice'" not in definition:
                        raise RuntimeError(
                            "incomplete PostgreSQL fencing migration: invalid writer "
                            f"constraint {SCHEMA}.{constraint_name}"
                        )

                _validate_runtime_fencing_privilege_scope(cursor)
        finally:
            conn.close()

    await asyncio.to_thread(_validate)


def _advisory_key(kind: str, *parts: str) -> int:
    payload = "\x1f".join((kind, *parts)).encode("utf-8")
    raw = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
    return raw - (1 << 64) if raw >= (1 << 63) else raw


def _json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise RuntimeError("Corrupt PostgreSQL fenced-session result")


_TERMINAL_OUTBOX_STATES = frozenset({"completed", "stale", "failed", "conflict"})


@dataclass(frozen=True)
class FencedOperationRecord:
    """Principal-scoped operation receipt returned by submit/status polling."""

    operation_id: str
    operation: str
    resource_id: str
    session_scope_id: str
    turn_id: str
    digest: str
    fencing_token: int
    state: str
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    replayed: bool = False

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_OUTBOX_STATES


_RECEIPT_SELECT = """
    SELECT operation_id, operation, resource_id, session_scope_id, turn_id,
           digest, fencing_token, state, result, error
    FROM openviking_fencing.operation_receipt
"""


def _operation_record(
    row: Any,
    *,
    replayed: bool = False,
) -> FencedOperationRecord:
    if row is None or len(row) != 10:
        raise RuntimeError("Corrupt PostgreSQL fenced-session receipt")
    result = None if row[8] is None else _json_value(row[8])
    error = None if row[9] is None else _json_value(row[9])
    return FencedOperationRecord(
        operation_id=str(row[0]),
        operation=str(row[1]),
        resource_id=str(row[2]),
        session_scope_id=str(row[3]),
        turn_id=str(row[4]),
        digest=str(row[5]),
        fencing_token=int(row[6]),
        state=str(row[7]),
        result=result,
        error=error,
        replayed=replayed,
    )


def _stale_error(
    *,
    highest_fencing_token: int,
    received_fencing_token: int,
) -> dict[str, Any]:
    return {
        "code": "CONFLICT",
        "message": "Fencing token is stale",
        "details": {
            "reason": "stale_fence",
            "highest_fencing_token": highest_fencing_token,
            "received_fencing_token": received_fencing_token,
        },
    }


class PostgresFencedOperationQueue:
    """Atomically accept fenced operations without executing their effects.

    The only request-side durable boundary is this short PostgreSQL
    transaction.  It owns no AGFS callback and never holds a database lock
    while waiting for the outbox writer.
    """

    def __init__(self, ctx: RequestContext, envelope: FencedOperationEnvelope):
        self._ctx = ctx
        self._envelope = envelope

    @property
    def _scope_key(self) -> tuple[str, str, str, str]:
        return (
            self._ctx.account_id,
            self._ctx.user.user_id,
            self._envelope.writer,
            self._envelope.session_scope_id,
        )

    @property
    def _principal_key(self) -> tuple[str, str, str]:
        return (
            self._ctx.account_id,
            self._ctx.user.user_id,
            self._envelope.writer,
        )

    def _select_current_receipt(self, cursor) -> FencedOperationRecord:
        cursor.execute(
            _RECEIPT_SELECT
            + """
              WHERE account_id=%s AND user_id=%s AND writer=%s
                AND session_scope_id=%s AND operation_id=%s
            """,
            (*self._scope_key, self._envelope.operation_id),
        )
        return _operation_record(cursor.fetchone())

    def _advance_scope(
        self,
        cursor,
        *,
        previous_highest: int,
    ) -> bool:
        """Advance the authoritative scope watermark in the accept transaction."""
        token = self._envelope.fencing_token
        if token <= previous_highest:
            return False
        cursor.execute(
            f"""
            UPDATE {SCHEMA}.scope_state
            SET highest_fencing_token=%s, active_turn_id=%s, updated_at=now()
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s
            """,
            (token, self._envelope.turn_id, *self._scope_key),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("PostgreSQL fencing scope advance CAS failed")
        return True

    def _suppress_stale_queued_sync(self, cursor) -> list[str]:
        """Erase pre-effect stale payloads after scope advance is committed.

        This reconciliation is deliberately outside the scope-watermark
        transaction.  Holding scope_state while waiting for another
        operation's receipt can deadlock an authorizing writer, whose global
        order is receipt -> scope -> outbox.  Here no scope row is held and
        every candidate follows receipt -> outbox, matching claim/completion.
        The already committed watermark remains the safety authority if this
        best-effort eager cleanup is interrupted; the writer will suppress the
        row during authorization.
        """
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        token = self._envelope.fencing_token
        cursor.execute(
            f"""
            SELECT operation_id, operation
            FROM {SCHEMA}.effect_outbox
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s
              AND fencing_token < %s
              AND operation_id <> %s
              AND state='queued'
              AND effect_started_at IS NULL
            ORDER BY sequence_id
            """,
            (*self._scope_key, token, self._envelope.operation_id),
        )
        candidates = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        suppressed: list[str] = []
        for operation_id, operation in candidates:
            cursor.execute(
                f"""
                SELECT state
                FROM {SCHEMA}.operation_receipt
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s
                  AND operation_id=%s
                FOR UPDATE
                """,
                (*self._scope_key, operation_id),
            )
            receipt = cursor.fetchone()
            if receipt is None or str(receipt[0]) != "queued":
                continue
            cursor.execute(
                f"""
                SELECT fencing_token
                FROM {SCHEMA}.effect_outbox
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND operation_id=%s
                  AND fencing_token < %s AND state='queued'
                  AND effect_started_at IS NULL
                FOR UPDATE
                """,
                (*self._scope_key, operation_id, token),
            )
            outbox = cursor.fetchone()
            if outbox is None:
                continue
            current_stale_token = int(outbox[0])
            cursor.execute(
                f"""
                UPDATE {SCHEMA}.operation_receipt
                SET state='stale', error=%s, updated_at=now()
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND operation_id=%s
                  AND state='queued'
                """,
                (
                    Json(
                        _stale_error(
                            highest_fencing_token=token,
                            received_fencing_token=current_stale_token,
                        )
                    ),
                    *self._scope_key,
                    operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("PostgreSQL stale receipt CAS failed")
            cursor.execute(
                f"""
                DELETE FROM {SCHEMA}.effect_outbox
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND operation_id=%s
                  AND state='queued' AND effect_started_at IS NULL
                """,
                (*self._scope_key, operation_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("PostgreSQL stale outbox delete CAS failed")
            # `operation` is a fixed protocol enum at ingress and therefore a
            # bounded metric label.  Tokens are never exposed as labels.
            suppressed.append(operation)
        return suppressed

    def _commit_and_suppress(self, conn, *, scope_advanced: bool) -> None:
        conn.commit()
        if not scope_advanced:
            return
        try:
            with conn.cursor() as cursor:
                suppressed = self._suppress_stale_queued_sync(cursor)
            conn.commit()
        except Exception:
            conn.rollback()
            # Acceptance and the authoritative watermark are already durable.
            # The writer will make the same suppression decision, so cleanup
            # failure must not turn a successful submit into a false negative.
            logger.exception("PostgreSQL eager stale cleanup failed after durable submit")
            return
        _record_suppressed_effect_metrics(suppressed)

    def _submit_sync(
        self,
        operation: str,
        resource_id: str,
        digest: str,
        request_payload: dict[str, Any],
    ) -> FencedOperationRecord:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        account_id, user_id, writer, scope_id = self._scope_key
        operation_id = self._envelope.operation_id
        turn_id = self._envelope.turn_id
        token = self._envelope.fencing_token
        conn = _connect(application_name="openviking-fenced-submit")
        scope_advanced = False
        try:
            conn.autocommit = False
            with conn.cursor() as cursor:
                # All locks are transaction scoped and the connection has a
                # DB-enforced idle-in-transaction timeout.  The operation lock
                # also serializes an operation_id accidentally reused across
                # two session scopes for the same authenticated principal.
                lock_keys = sorted(
                    {
                        _advisory_key(
                            "operation",
                            account_id,
                            user_id,
                            writer,
                            operation_id,
                        ),
                        _advisory_key("scope", account_id, user_id, writer, scope_id),
                        _advisory_key("session", account_id, user_id, resource_id),
                    }
                )
                for key in lock_keys:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

                from openviking.server import fenced_operation  # noqa: PLC0415

                fenced_operation.after_fenced_submit_locks_acquired(operation)

                cursor.execute(
                    _RECEIPT_SELECT
                    + """
                      WHERE account_id=%s AND user_id=%s AND writer=%s
                        AND operation_id=%s
                      FOR UPDATE
                    """,
                    (*self._principal_key, operation_id),
                )
                principal_receipts = cursor.fetchall()
                if len(principal_receipts) > 1:
                    raise FencedOperationConflict(
                        "Operation ID is not uniquely scoped to this principal",
                        reason="operation_scope_conflict",
                        details={"operation_id": operation_id},
                    )
                receipt = (
                    _operation_record(principal_receipts[0], replayed=True)
                    if principal_receipts
                    else None
                )
                if receipt is not None:
                    if receipt.session_scope_id != scope_id:
                        raise FencedOperationConflict(
                            "Operation ID was already used by another session scope",
                            reason="operation_scope_conflict",
                            details={"operation_id": operation_id},
                        )
                    if receipt.digest != digest:
                        raise FencedOperationConflict(
                            "Operation ID was already used for different content",
                            reason="operation_digest_conflict",
                            details={"operation_id": operation_id},
                        )

                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.scope_state
                        (account_id,user_id,writer,session_scope_id,
                         highest_fencing_token,active_turn_id)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (account_id,user_id,writer,session_scope_id)
                    DO NOTHING
                    """,
                    (*self._scope_key, token, turn_id),
                )
                cursor.execute(
                    f"""
                    SELECT highest_fencing_token, active_turn_id
                    FROM {SCHEMA}.scope_state
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s
                    FOR UPDATE
                    """,
                    self._scope_key,
                )
                scope_row = cursor.fetchone()
                if scope_row is None:
                    raise RuntimeError("PostgreSQL fencing scope row disappeared")
                highest, active_turn = int(scope_row[0]), str(scope_row[1])
                # A completed exact replay at or below its persisted receipt
                # token only returns the immutable result.  It must not be
                # rejected merely because a later business turn is active,
                # and it must not mutate either watermark or receipt token.
                completed_pure_replay = bool(
                    receipt is not None
                    and receipt.state == "completed"
                    and token <= receipt.fencing_token
                )
                if not completed_pure_replay and token < highest:
                    raise FencedOperationConflict(
                        "Fencing token is stale",
                        reason="stale_fence",
                        details={
                            "highest_fencing_token": highest,
                            "received_fencing_token": token,
                        },
                    )
                if not completed_pure_replay and token == highest and turn_id != active_turn:
                    raise FencedOperationConflict(
                        "The active fencing token is already bound to another turn",
                        reason="turn_fence_conflict",
                        details={"active_turn_id": active_turn},
                    )
                replay_can_advance = bool(
                    receipt is not None
                    and (not receipt.terminal or receipt.state == "completed")
                    and token > receipt.fencing_token
                )
                if replay_can_advance and (
                    active_turn != receipt.turn_id or highest != receipt.fencing_token
                ):
                    raise FencedOperationConflict(
                        "A replay cannot replace a newer active turn or operation fence",
                        reason="turn_fence_conflict",
                        details={"active_turn_id": active_turn},
                    )

                cursor.execute(
                    f"""
                    SELECT writer, session_scope_id
                    FROM {SCHEMA}.session_binding
                    WHERE account_id=%s AND user_id=%s AND session_id=%s
                    FOR UPDATE
                    """,
                    (account_id, user_id, resource_id),
                )
                binding = cursor.fetchone()
                if binding is not None and (str(binding[0]), str(binding[1])) != (
                    writer,
                    scope_id,
                ):
                    raise FencedOperationConflict(
                        "Session is already bound to a different writer scope",
                        reason="session_scope_conflict",
                        details={"session_id": resource_id},
                    )

                # Exact terminal replay wins over a later commit closure.  This
                # is the response-loss path: the message/used effect already
                # completed, then a commit closed the turn before the caller
                # retried the original operation_id.
                if receipt is not None and receipt.terminal:
                    if receipt.digest != digest:
                        raise FencedOperationConflict(
                            "Operation ID was already used for different content",
                            reason="operation_digest_conflict",
                            details={"operation_id": operation_id},
                        )
                    if receipt.state == "completed" and not completed_pure_replay:
                        scope_advanced = self._advance_scope(
                            cursor,
                            previous_highest=highest,
                        )
                        cursor.execute(
                            f"""
                            UPDATE {SCHEMA}.operation_receipt
                            SET fencing_token=GREATEST(fencing_token,%s),
                                updated_at=now()
                            WHERE account_id=%s AND user_id=%s AND writer=%s
                              AND session_scope_id=%s AND operation_id=%s
                            """,
                            (token, *self._scope_key, operation_id),
                        )
                        cursor.execute(
                            f"""
                            UPDATE {SCHEMA}.effect_receipt
                            SET fencing_token=GREATEST(fencing_token,%s)
                            WHERE account_id=%s AND user_id=%s AND writer=%s
                              AND session_scope_id=%s AND operation_id=%s
                              AND digest=%s
                            """,
                            (
                                token,
                                *self._scope_key,
                                operation_id,
                                digest,
                            ),
                        )
                    # failed/stale/conflict are immutable terminal outcomes.
                    # A higher fence must use a new operation_id to retry.
                    self._commit_and_suppress(conn, scope_advanced=scope_advanced)
                    current = self._select_current_receipt(cursor)
                    return replace(current, replayed=True)

                # wait=true publishes the commit closure and moves Phase 2 to
                # commit_work_outbox while intentionally keeping the operation
                # receipt running.  An exact HTTP retry must observe that same
                # running receipt; treating the closure as a completed replay
                # would bypass the caller's wait contract and could orphan the
                # durable work row.
                if receipt is not None and receipt.state == "running" and operation == "commit":
                    cursor.execute(
                        f"""
                        SELECT task_id,archive_uri,wait_for_completion
                        FROM {SCHEMA}.commit_work_outbox
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                        FOR UPDATE
                        """,
                        (*self._scope_key, operation_id),
                    )
                    commit_work = cursor.fetchone()
                    if commit_work is not None:
                        result = receipt.result or {}
                        if (
                            not bool(commit_work[2])
                            or result.get("task_id") != str(commit_work[0])
                            or result.get("archive_uri") != str(commit_work[1])
                        ):
                            raise RuntimeError("PostgreSQL fenced wait receipt/work mismatch")
                        scope_advanced = self._advance_scope(
                            cursor,
                            previous_highest=highest,
                        )
                        cursor.execute(
                            f"""
                            UPDATE {SCHEMA}.operation_receipt
                            SET fencing_token=GREATEST(fencing_token,%s),
                                updated_at=now()
                            WHERE account_id=%s AND user_id=%s AND writer=%s
                              AND session_scope_id=%s AND operation_id=%s
                              AND digest=%s AND state='running'
                            """,
                            (
                                token,
                                *self._scope_key,
                                operation_id,
                                digest,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError("PostgreSQL fenced wait replay CAS failed")
                        self._commit_and_suppress(conn, scope_advanced=scope_advanced)
                        current = self._select_current_receipt(cursor)
                        return replace(current, replayed=True)

                cursor.execute(
                    f"""
                    SELECT operation_id, digest, result
                    FROM {SCHEMA}.session_turn_closure
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND turn_id=%s AND session_id=%s
                    FOR UPDATE
                    """,
                    (*self._scope_key, turn_id, resource_id),
                )
                closure = cursor.fetchone()
                if closure is not None:
                    closure_operation_id, closure_digest, closure_result = closure
                    if operation == "commit" and str(closure_operation_id) == operation_id:
                        if str(closure_digest) != digest:
                            raise FencedOperationConflict(
                                "Operation ID was already used for different content",
                                reason="operation_digest_conflict",
                                details={"operation_id": operation_id},
                            )
                        scope_advanced = self._advance_scope(
                            cursor,
                            previous_highest=highest,
                        )
                        result = _json_value(closure_result)
                        cursor.execute(
                            f"""
                            INSERT INTO {SCHEMA}.operation_receipt
                                (account_id,user_id,writer,session_scope_id,
                                 operation_id,operation,resource_id,turn_id,digest,
                                 fencing_token,state,result,error)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                    'completed',%s,NULL)
                            ON CONFLICT (
                                account_id,user_id,writer,session_scope_id,operation_id
                            ) DO UPDATE SET state='completed', result=EXCLUDED.result,
                                            error=NULL,
                                            fencing_token=EXCLUDED.fencing_token,
                                            updated_at=now()
                            """,
                            (
                                *self._scope_key,
                                operation_id,
                                operation,
                                resource_id,
                                turn_id,
                                digest,
                                token,
                                Json(result),
                            ),
                        )
                        self._commit_and_suppress(conn, scope_advanced=scope_advanced)
                        return self._select_current_receipt(cursor)
                    if operation in {"message", "used", "commit"}:
                        raise FencedOperationConflict(
                            "The session is closed for this turn",
                            reason="session_turn_closed",
                            details={"session_id": resource_id, "turn_id": turn_id},
                        )

                scope_advanced = self._advance_scope(
                    cursor,
                    previous_highest=highest,
                )

                if receipt is not None:
                    if receipt.digest != digest:
                        raise FencedOperationConflict(
                            "Operation ID was already used for different content",
                            reason="operation_digest_conflict",
                            details={"operation_id": operation_id},
                        )
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.operation_receipt
                        SET fencing_token=GREATEST(fencing_token,%s), updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                        """,
                        (token, *self._scope_key, operation_id),
                    )
                    if receipt.state in {"queued", "running"}:
                        cursor.execute(
                            f"""
                            UPDATE {SCHEMA}.effect_outbox
                            SET fencing_token=GREATEST(fencing_token,%s),
                                updated_at=now()
                            WHERE account_id=%s AND user_id=%s AND writer=%s
                              AND session_scope_id=%s AND operation_id=%s
                              AND digest=%s
                            """,
                            (
                                token,
                                *self._scope_key,
                                operation_id,
                                digest,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError("PostgreSQL fenced outbox receipt is missing")
                    self._commit_and_suppress(conn, scope_advanced=scope_advanced)
                    current = self._select_current_receipt(cursor)
                    return replace(current, replayed=True)

                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.operation_receipt
                        (account_id,user_id,writer,session_scope_id,operation_id,
                         operation,resource_id,turn_id,digest,fencing_token,state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued')
                    """,
                    (
                        *self._scope_key,
                        operation_id,
                        operation,
                        resource_id,
                        turn_id,
                        digest,
                        token,
                    ),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.effect_outbox
                        (account_id,user_id,writer,session_scope_id,operation_id,
                         operation,resource_id,turn_id,digest,fencing_token,
                         request_payload,actor_peer_id,state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued')
                    """,
                    (
                        *self._scope_key,
                        operation_id,
                        operation,
                        resource_id,
                        turn_id,
                        digest,
                        token,
                        Json(request_payload),
                        self._ctx.actor_peer_id,
                    ),
                )
            self._commit_and_suppress(conn, scope_advanced=scope_advanced)
            with conn.cursor() as cursor:
                return self._select_current_receipt(cursor)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    async def submit(
        self,
        operation: str,
        resource_id: str,
        *,
        request_payload: Optional[dict[str, Any]] = None,
    ) -> FencedOperationRecord:
        """Durably queue one effect; no OpenViking/AGFS callback runs here."""
        started_at = time.monotonic()
        expected_payload = self._envelope.model_dump(mode="json")
        payload = expected_payload if request_payload is None else request_payload
        if json.dumps(payload, sort_keys=True, separators=(",", ":")) != json.dumps(
            expected_payload,
            sort_keys=True,
            separators=(",", ":"),
        ):
            raise ValueError("request_payload must exactly match the validated envelope")
        digest = operation_digest(
            operation,
            self._envelope,
            resource_id=resource_id,
            actor_peer_id=self._ctx.actor_peer_id,
        )

        # Strict fault-injection seam: a frozen Alice/API owner here owns no DB
        # transaction and cannot execute an external effect.  A higher fence can
        # submit, after which this owner resumes and is rejected as stale.
        from openviking.server import fenced_operation  # noqa: PLC0415

        seam_result = fenced_operation.after_fenced_submit_preflight(operation)
        if inspect.isawaitable(seam_result):
            await seam_result
        try:
            record = await asyncio.to_thread(
                self._submit_sync,
                operation,
                resource_id,
                digest,
                payload,
            )
            outcome = (
                "replayed"
                if record.replayed
                else (
                    record.state if record.state in {"stale", "conflict", "failed"} else "accepted"
                )
            )
            _record_v2_submit_metric(operation, outcome, started_at)
            return record
        except FencedOperationConflict as exc:
            reason = str((getattr(exc, "details", None) or {}).get("reason") or "")
            _record_v2_submit_metric(
                operation,
                "stale" if reason == "stale_fence" else "conflict",
                started_at,
            )
            raise
        except FailedPreconditionError:
            _record_v2_submit_metric(operation, "rejected", started_at)
            raise
        except Exception as exc:
            _record_v2_submit_metric(operation, "error", started_at)
            logger.exception("PostgreSQL fenced outbox submit failed")
            raise UnavailableError("PostgreSQL fenced outbox", "persistence_unavailable") from exc

    @classmethod
    def _get_sync(
        cls,
        ctx: RequestContext,
        operation_id: str,
        *,
        writer: str = "alice",
    ) -> Optional[FencedOperationRecord]:
        conn = _connect(application_name="openviking-fenced-status")
        try:
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute(
                    _RECEIPT_SELECT
                    + """
                      WHERE account_id=%s AND user_id=%s AND writer=%s
                        AND operation_id=%s
                    """,
                    (ctx.account_id, ctx.user.user_id, writer, operation_id),
                )
                rows = cursor.fetchall()
                if not rows:
                    return None
                if len(rows) != 1:
                    raise FencedOperationConflict(
                        "Operation ID is not uniquely scoped to this principal",
                        reason="operation_scope_conflict",
                        details={"operation_id": operation_id},
                    )
                # A status read is not itself a duplicate submission.  POST
                # preserves duplicate-vs-new semantics from `_submit_sync`;
                # polling reports only the durable operation state.
                return _operation_record(rows[0], replayed=False)
        finally:
            conn.close()

    @classmethod
    async def get(
        cls,
        ctx: RequestContext,
        operation_id: str,
        *,
        writer: str = "alice",
    ) -> Optional[FencedOperationRecord]:
        try:
            return await asyncio.to_thread(
                cls._get_sync,
                ctx,
                operation_id,
                writer=writer,
            )
        except FencedOperationConflict:
            raise
        except Exception as exc:
            logger.exception("PostgreSQL fenced operation status read failed")
            raise UnavailableError("PostgreSQL fenced outbox", "persistence_unavailable") from exc

    @classmethod
    async def wait(
        cls,
        ctx: RequestContext,
        operation_id: str,
        *,
        writer: str = "alice",
        timeout_seconds: float,
        poll_seconds: float = 0.05,
    ) -> Optional[FencedOperationRecord]:
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
        while True:
            record = await cls.get(ctx, operation_id, writer=writer)
            if record is None or record.terminal:
                return record
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return record
            await asyncio.sleep(min(max(0.01, poll_seconds), remaining))


class PostgresFencedOperationLedger:
    """One request-scoped PostgreSQL fencing transaction."""

    def __init__(self, ctx: RequestContext, envelope: FencedOperationEnvelope):
        self._ctx = ctx
        self._envelope = envelope

    @property
    def _scope_key(self) -> tuple[str, str, str, str]:
        return (
            self._ctx.account_id,
            self._ctx.user.user_id,
            self._envelope.writer,
            self._envelope.session_scope_id,
        )

    def _begin_and_prepare(
        self,
        conn,
        operation: str,
        resource_id: str,
        digest: str,
    ) -> tuple[Optional[dict[str, Any]], bool]:
        """Acquire non-expiring transaction locks and prepare the operation."""
        account_id, user_id, writer, scope_id = self._scope_key
        token = self._envelope.fencing_token
        turn_id = self._envelope.turn_id
        operation_id = self._envelope.operation_id
        conn.autocommit = False
        with conn.cursor() as cursor:
            # Prevent a database-side idle timeout from turning this into another
            # expiring lease while the async OpenViking effect is running.
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = 0")
            lock_keys = sorted(
                {
                    _advisory_key("scope", account_id, user_id, writer, scope_id),
                    _advisory_key("session", account_id, user_id, resource_id),
                }
            )
            for key in lock_keys:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

            cursor.execute(
                f"""
                INSERT INTO {SCHEMA}.scope_state
                    (account_id, user_id, writer, session_scope_id,
                     highest_fencing_token, active_turn_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_id, user_id, writer, session_scope_id)
                DO NOTHING
                """,
                (*self._scope_key, token, turn_id),
            )
            cursor.execute(
                f"""
                SELECT highest_fencing_token, active_turn_id
                FROM {SCHEMA}.scope_state
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s
                FOR UPDATE
                """,
                self._scope_key,
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL fencing scope row disappeared")
            highest, active_turn = int(row[0]), str(row[1])
            if token < highest:
                raise FencedOperationConflict(
                    "Fencing token is stale",
                    reason="stale_fence",
                    details={
                        "highest_fencing_token": highest,
                        "received_fencing_token": token,
                    },
                )
            if token == highest and turn_id != active_turn:
                raise FencedOperationConflict(
                    "The active fencing token is already bound to another turn",
                    reason="turn_fence_conflict",
                    details={"active_turn_id": active_turn},
                )

            cursor.execute(
                f"""
                SELECT writer, session_scope_id
                FROM {SCHEMA}.session_binding
                WHERE account_id=%s AND user_id=%s AND session_id=%s
                FOR UPDATE
                """,
                (account_id, user_id, resource_id),
            )
            binding = cursor.fetchone()
            binding_missing = binding is None
            if binding is not None and (str(binding[0]), str(binding[1])) != (
                writer,
                scope_id,
            ):
                raise FencedOperationConflict(
                    "Session is already bound to a different writer scope",
                    reason="session_scope_conflict",
                    details={"session_id": resource_id},
                )

            receipt_key = (*self._scope_key, operation_id)
            cursor.execute(
                f"""
                SELECT digest, state, result, fencing_token
                FROM {SCHEMA}.operation_receipt
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND operation_id=%s
                FOR UPDATE
                """,
                receipt_key,
            )
            receipt = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT digest, result, fencing_token
                FROM {SCHEMA}.effect_receipt
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND operation_id=%s
                FOR UPDATE
                """,
                receipt_key,
            )
            effect = cursor.fetchone()

            for persisted in (receipt, effect):
                if persisted is not None and str(persisted[0]) != digest:
                    raise FencedOperationConflict(
                        "Operation ID was already used for different content",
                        reason="operation_digest_conflict",
                        details={"operation_id": operation_id},
                    )

            # A commit closure is the durable effect manifest even after normal
            # operation/effect receipts from an older fence have been pruned.
            cursor.execute(
                f"""
                SELECT operation_id, digest, result
                FROM {SCHEMA}.session_turn_closure
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND turn_id=%s AND session_id=%s
                FOR UPDATE
                """,
                (*self._scope_key, turn_id, resource_id),
            )
            closure = cursor.fetchone()
            if closure is not None:
                closure_operation_id, closure_digest, closure_result = closure
                if operation == "commit" and str(closure_operation_id) == operation_id:
                    if str(closure_digest) != digest:
                        raise FencedOperationConflict(
                            "Operation ID was already used for different content",
                            reason="operation_digest_conflict",
                            details={"operation_id": operation_id},
                        )
                    cached = _json_value(closure_result)
                    self._advance_and_prune(cursor, highest, active_turn)
                    return cached, True
                if operation in {"message", "used", "commit"}:
                    raise FencedOperationConflict(
                        "The session is closed for this turn",
                        reason="session_turn_closed",
                        details={"session_id": resource_id, "turn_id": turn_id},
                    )

            cached: Optional[dict[str, Any]] = None
            if receipt is not None and str(receipt[1]) == "done":
                cached = _json_value(receipt[2])
            elif effect is not None:
                cached = _json_value(effect[1])
            if cached is not None:
                self._advance_and_prune(cursor, highest, active_turn)
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.operation_receipt
                    SET fencing_token=%s, updated_at=now()
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                    """,
                    (token, *receipt_key),
                )
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.effect_receipt
                    SET fencing_token=%s
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                    """,
                    (token, *receipt_key),
                )
                return cached, True

            self._advance_and_prune(cursor, highest, active_turn)
            cursor.execute(
                f"""
                INSERT INTO {SCHEMA}.operation_receipt
                    (account_id, user_id, writer, session_scope_id,
                     operation_id, operation, resource_id, turn_id, digest,
                     fencing_token, state, result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'prepared',NULL)
                ON CONFLICT (account_id,user_id,writer,session_scope_id,operation_id)
                DO UPDATE SET fencing_token=EXCLUDED.fencing_token,
                              updated_at=now()
                """,
                (
                    *receipt_key,
                    operation,
                    resource_id,
                    turn_id,
                    digest,
                    token,
                ),
            )
            return None, binding_missing

    def _advance_and_prune(self, cursor, highest: int, active_turn: str) -> None:
        account_id, user_id, writer, scope_id = self._scope_key
        token = self._envelope.fencing_token
        turn_id = self._envelope.turn_id
        operation_id = self._envelope.operation_id
        if token > highest:
            cursor.execute(
                f"""
                UPDATE {SCHEMA}.scope_state
                SET highest_fencing_token=%s, active_turn_id=%s, updated_at=now()
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s
                """,
                (token, turn_id, *self._scope_key),
            )
            for table in ("operation_receipt", "effect_receipt"):
                cursor.execute(
                    f"""
                    DELETE FROM {SCHEMA}.{table}
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND fencing_token < %s
                      AND operation_id <> %s
                    """,
                    (*self._scope_key, token, operation_id),
                )
            if turn_id != active_turn:
                cursor.execute(
                    f"""
                    DELETE FROM {SCHEMA}.session_turn_closure
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND turn_id <> %s
                    """,
                    (*self._scope_key, turn_id),
                )

    def _complete(
        self,
        conn,
        operation: str,
        resource_id: str,
        digest: str,
        result: dict[str, Any],
        binding_missing: bool,
    ) -> None:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        account_id, user_id, writer, scope_id = self._scope_key
        operation_id = self._envelope.operation_id
        turn_id = self._envelope.turn_id
        token = self._envelope.fencing_token
        receipt_key = (*self._scope_key, operation_id)
        with conn.cursor() as cursor:
            if binding_missing:
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.session_binding
                        (account_id,user_id,session_id,writer,session_scope_id)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (account_id,user_id,session_id) DO NOTHING
                    """,
                    (account_id, user_id, resource_id, writer, scope_id),
                )
                cursor.execute(
                    f"""
                    SELECT writer, session_scope_id
                    FROM {SCHEMA}.session_binding
                    WHERE account_id=%s AND user_id=%s AND session_id=%s
                    """,
                    (account_id, user_id, resource_id),
                )
                binding = cursor.fetchone()
                if binding is None or (str(binding[0]), str(binding[1])) != (
                    writer,
                    scope_id,
                ):
                    raise FencedOperationConflict(
                        "Session is already bound to a different writer scope",
                        reason="session_scope_conflict",
                        details={"session_id": resource_id},
                    )

            cursor.execute(
                f"""
                UPDATE {SCHEMA}.operation_receipt
                SET state='done', result=%s, fencing_token=%s, updated_at=now()
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND operation_id=%s AND digest=%s
                """,
                (Json(result), token, *receipt_key, digest),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("PostgreSQL fenced operation CAS failed")
            cursor.execute(
                f"""
                INSERT INTO {SCHEMA}.effect_receipt
                    (account_id,user_id,writer,session_scope_id,operation_id,
                     operation,resource_id,turn_id,digest,fencing_token,result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (account_id,user_id,writer,session_scope_id,operation_id)
                DO UPDATE SET result=EXCLUDED.result,
                              fencing_token=EXCLUDED.fencing_token,
                              completed_at=now()
                WHERE {SCHEMA}.effect_receipt.digest=EXCLUDED.digest
                """,
                (
                    *receipt_key,
                    operation,
                    resource_id,
                    turn_id,
                    digest,
                    token,
                    Json(result),
                ),
            )
            if cursor.rowcount != 1:
                raise FencedOperationConflict(
                    "Operation ID was already used for different content",
                    reason="operation_digest_conflict",
                    details={"operation_id": operation_id},
                )
            if operation == "commit":
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.session_turn_closure
                        (account_id,user_id,writer,session_scope_id,turn_id,
                         session_id,operation_id,digest,fencing_token,result)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (
                        account_id,user_id,writer,session_scope_id,turn_id,session_id
                    ) DO NOTHING
                    """,
                    (
                        *self._scope_key,
                        turn_id,
                        resource_id,
                        operation_id,
                        digest,
                        token,
                        Json(result),
                    ),
                )
                if cursor.rowcount != 1:
                    raise FencedOperationConflict(
                        "The session is already closed for this turn",
                        reason="session_turn_closed",
                        details={"session_id": resource_id, "turn_id": turn_id},
                    )

    async def execute(
        self,
        operation: str,
        resource_id: str,
        callback: Callable[[], Awaitable[dict[str, Any]]],
    ) -> tuple[dict[str, Any], bool]:
        """Execute one effect under a non-expiring PostgreSQL transaction lock."""
        digest = operation_digest(operation, self._envelope, resource_id=resource_id)
        try:
            conn = await asyncio.to_thread(_connect)
        except Exception as exc:
            logger.exception("PostgreSQL legacy fencing store connection failed")
            raise UnavailableError("PostgreSQL fencing store", "persistence_unavailable") from exc
        try:
            try:
                cached, binding_missing = await asyncio.to_thread(
                    self._begin_and_prepare,
                    conn,
                    operation,
                    resource_id,
                    digest,
                )
                if cached is not None:
                    await asyncio.to_thread(conn.commit)
                    return cached, True

                result = await callback()
                if not isinstance(result, dict):
                    raise TypeError("Fenced operation callback must return a dict")
                # Resolve through the module so tests (and fault-injection
                # harnesses) can replace the crash seam after import.
                from openviking.server import fenced_operation  # noqa: PLC0415

                seam_result = fenced_operation.after_fenced_effect_before_receipt(operation)
                if inspect.isawaitable(seam_result):
                    await seam_result
                await asyncio.to_thread(
                    self._complete,
                    conn,
                    operation,
                    resource_id,
                    digest,
                    result,
                    binding_missing,
                )
                await asyncio.to_thread(conn.commit)
                return result, False
            except (FencedOperationConflict, FailedPreconditionError):
                await asyncio.to_thread(conn.rollback)
                raise
            except Exception:
                await asyncio.to_thread(conn.rollback)
                raise
        finally:
            await asyncio.to_thread(conn.close)
