# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MySQL Watch store shared by every node of a distributed deployment.

Each occupancy transition is a single compare-and-set statement and every node
compares times against the database clock. Private ``auth_state`` and
``connector_states`` are stored AES-GCM encrypted under
``credential_encryption_key``. The tables are provisioned from
``openviking/service/sql/001_watch_tasks.sql``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Optional, Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
    and_,
    case,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

from openviking.resource.watch_store import (
    WatchMoveResult,
    WatchStore,
    is_connector,
    parent_uri,
    target_conflict_message,
)
from openviking.resource.watch_types import (
    PAYLOAD_FIELDS,
    PermissionDeniedError,
    WatchAuthority,
    WatchPatch,
    WatchRun,
    WatchRunResult,
    WatchState,
    WatchTask,
)
from openviking.storage.mysql_runtime import (
    get_shared_mysql_runtime,
    mysql_runtime_store,
    wait_mysql_cleanup,
)
from openviking_cli.exceptions import ConflictError
from openviking_cli.utils.config.config_utils import normalize_service_name

_COLUMN_FIELDS = frozenset(
    {
        "task_id",
        "account_id",
        "user_id",
        "original_role",
        "to_uri",
        "is_active",
        "watch_interval",
        "created_at",
        "next_execution_time",
        "last_execution_time",
        "last_task_id",
        "last_status",
        "last_error",
        "auth_state",
        "connector_states",
    }
)


class db_now(FunctionElement):
    """Database clock in epoch seconds; every node compares against one clock."""

    type = Float()
    inherit_cache = True


@compiles(db_now, "mysql")
def _mysql_now(element: Any, compiler: Any, **kw: Any) -> str:
    return "UNIX_TIMESTAMP(NOW(6))"


def _hash(value: Optional[str]) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def _epoch(value: Optional[datetime]) -> Optional[float]:
    return value.timestamp() if value else None


def _datetime(value: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(value) if value is not None else None


_SECRET_PREFIX = "aesgcm:v1:"
_SECRET_FIELDS = ("auth_state", "connector_states")


class _SecretCipher:
    """AES-GCM for private Watch state, bound to its task ID and field."""

    def __init__(self, encryption_key: object) -> None:
        if not isinstance(encryption_key, str):
            raise ValueError(
                "MySQL Watch store requires credential_encryption_key (base64-encoded 32 bytes)"
            )
        try:
            key = base64.b64decode(encryption_key.encode(), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError, UnicodeError) as exc:
            raise ValueError("credential_encryption_key must be base64-encoded") from exc
        if len(key) != 32:
            raise ValueError("credential_encryption_key must decode to exactly 32 bytes")
        self._aead = AESGCM(key)

    @staticmethod
    def _associated_data(task_id: str, field: str) -> bytes:
        return f"{task_id}\0{field}".encode()

    def seal(self, task_id: str, field: str, value: Optional[dict[str, Any]]) -> Optional[str]:
        if value is None:
            return None
        nonce = secrets.token_bytes(12)
        ciphertext = self._aead.encrypt(
            nonce,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
            self._associated_data(task_id, field),
        )
        return _SECRET_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def open(self, task_id: str, field: str, material: Optional[str]) -> Optional[dict[str, Any]]:
        if material is None:
            return None
        try:
            if not material.startswith(_SECRET_PREFIX):
                raise ValueError("unknown secret format")
            payload = base64.urlsafe_b64decode(material[len(_SECRET_PREFIX) :].encode())
            plaintext = self._aead.decrypt(
                payload[:12], payload[12:], self._associated_data(task_id, field)
            )
            value = json.loads(plaintext)
        except (InvalidTag, ValueError, UnicodeError) as exc:
            raise RuntimeError(
                f"Cannot decrypt {field} of Watch {task_id}; check "
                "storage.watch_store.params.credential_encryption_key"
            ) from exc
        return value if isinstance(value, dict) else None


def watch_tables(table: str, metadata: Optional[MetaData] = None) -> Table:
    """Build the Watch task table. MySQL DDL lives in service/sql."""
    metadata = metadata or MetaData()
    key = String(255).with_variant(String(255, collation="utf8mb4_bin"), "mysql")
    tasks = Table(
        table,
        metadata,
        Column("service_name", key, primary_key=True),
        Column("task_id", key, primary_key=True),
        Column("account_id", key, nullable=False),
        Column("user_id", key, nullable=False),
        Column("original_role", String(64), nullable=False),
        Column("target_uri_hash", String(64), nullable=False),
        Column("target_uri", Text, nullable=True),
        Column("enabled", Boolean, nullable=False),
        Column("deleted", Boolean, nullable=False, default=False),
        Column("watch_interval", Float, nullable=False),
        Column("next_run_at", Float, nullable=True),
        Column("current_run_id", key, nullable=True),
        Column("run_started_at", Float, nullable=True),
        Column("run_fenced", Boolean, nullable=False, default=False),
        Column("last_execution_time", Float, nullable=True),
        Column("last_task_id", key, nullable=True),
        Column("last_status", String(64), nullable=True),
        Column("last_error", Text, nullable=True),
        Column("payload", JSON, nullable=False),
        Column("connector", Boolean, nullable=False, default=False),
        Column("auth_state", Text, nullable=True),
        Column("connector_states", Text, nullable=True),
        Column("created_at", Float, nullable=False),
        Column("updated_at", Float, nullable=False),
        Index(f"ix_{table}_due", "service_name", "enabled", "next_run_at"),
        Index(f"ix_{table}_run", "service_name", "current_run_id"),
        Index(f"ix_{table}_target", "service_name", "account_id", "target_uri_hash"),
        Index(f"ix_{table}_list", "service_name", "account_id", "user_id", "created_at"),
    )
    return tasks


@mysql_runtime_store
class MySQLWatchStore(WatchStore):
    def __init__(self, *, service_name: str, params: Optional[dict[str, Any]] = None) -> None:
        from openviking.service.task_store_mysql import MySQLTaskWorkStore
        from openviking.service.task_store_mysql_schema import validate_table_prefix

        values = dict(params or {})
        self._service_name = normalize_service_name(service_name)
        self._table_name = validate_table_prefix(values.pop("table", "ov_watch_tasks"))
        self._tasks = watch_tables(self._table_name)
        self._cipher = _SecretCipher(values.pop("credential_encryption_key", None))
        self._url, self._engine_options = MySQLTaskWorkStore._connection_options(values)
        self._mysql_runtime: Any = get_shared_mysql_runtime()
        self._engine: Any = None
        self._sessions: Any = None
        self._runtime_lease = False

    @property
    def provider(self) -> str:
        return "mysql"

    async def initialize(self) -> None:
        if self._engine is not None:
            return
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from openviking.storage.mysql_session import MySQLAsyncSession

        await self._mysql_runtime.acquire()
        self._runtime_lease = True
        engine = None
        try:
            engine = create_async_engine(self._url, **self._engine_options)
            sessions = async_sessionmaker(engine, class_=MySQLAsyncSession, expire_on_commit=False)
            async with sessions() as session:
                await session.execute(select(*self._tasks.c).limit(1))
        except BaseException:

            async def cleanup() -> None:
                if engine is not None:
                    await engine.dispose()
                self._runtime_lease = False
                await self._mysql_runtime.release()

            await wait_mysql_cleanup(cleanup())
            raise
        self._engine, self._sessions = engine, sessions

    async def close(self) -> None:
        async def cleanup() -> None:
            if self._engine is not None:
                await self._engine.dispose()
            self._engine = self._sessions = None
            if self._runtime_lease:
                self._runtime_lease = False
                await self._mysql_runtime.release()

        await wait_mysql_cleanup(cleanup())

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[Any]:
        if self._sessions is None:
            raise RuntimeError("MySQLWatchStore must be initialized")
        async with self._sessions() as session:
            async with session.begin():
                yield session

    # ── Row mapping ─────────────────────────────────────────────────────

    @property
    def _scope(self) -> Any:
        return self._tasks.c.service_name == self._service_name

    def _authority(self, authority: WatchAuthority) -> Any:
        if authority.root:
            return literal(True)
        c = self._tasks.c
        return and_(c.account_id == authority.account_id, c.user_id == authority.user_id)

    def _prefix(self, uri: str) -> Any:
        """Equality or a descendant on a path boundary, without LIKE escaping."""
        prefix = uri.rstrip("/")
        column = self._tasks.c.target_uri
        return or_(
            column == prefix,
            func.substr(column, 1, len(prefix) + 1) == prefix + "/",
        )

    def _task(self, row: Any) -> WatchTask:
        data = dict(row["payload"] or {})
        for name in ("task_id", "account_id", "user_id", "original_role"):
            data[name] = row[name]
        data["to_uri"] = row["target_uri"]
        data["is_active"] = bool(row["enabled"])
        data["watch_interval"] = float(row["watch_interval"])
        data["created_at"] = _datetime(row["created_at"])
        data["next_execution_time"] = _datetime(row["next_run_at"])
        data["last_execution_time"] = _datetime(row["last_execution_time"])
        for name in ("last_task_id", "last_status", "last_error"):
            data[name] = row[name]
        for name in _SECRET_FIELDS:
            data[name] = self._cipher.open(row["task_id"], name, row[name])
        return WatchTask.from_dict(data)

    def _state(self, row: Any) -> WatchState:
        return WatchState(
            task=self._task(row),
            current_run_id=row["current_run_id"],
            run_started_at=row["run_started_at"],
            deleted=bool(row["deleted"]),
            now=float(row["db_now"]),
            run_fenced=bool(row["run_fenced"]),
        )

    def _select_state(self) -> Any:
        return select(*self._tasks.c, db_now().label("db_now"))

    @staticmethod
    def _payload(task: WatchTask) -> dict[str, Any]:
        payload = task.to_storage_dict()
        for name in _COLUMN_FIELDS:
            payload.pop(name, None)
        return payload

    async def _rows(self, session: Any, statement: Any) -> list[Any]:
        return list((await session.execute(statement)).mappings().all())

    # ── API reads and writes ────────────────────────────────────────────

    async def get_state(self, task_id: str) -> Optional[WatchState]:
        c = self._tasks.c
        async with self._transaction() as session:
            rows = await self._rows(
                session, self._select_state().where(self._scope, c.task_id == task_id)
            )
            return self._state(rows[0]) if rows else None

    async def get_task(
        self,
        task_id: str,
        authority: WatchAuthority = WatchAuthority(),
    ) -> Optional[WatchTask]:
        c = self._tasks.c
        async with self._transaction() as session:
            rows = await self._rows(
                session,
                select(*self._tasks.c).where(
                    self._scope,
                    c.task_id == task_id,
                    c.deleted.is_(False),
                    self._authority(authority),
                ),
            )
            return self._task(rows[0]) if rows else None

    async def list_tasks(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool = False,
        scope_account_id: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> list[WatchTask]:
        c = self._tasks.c
        clauses = [self._scope, c.deleted.is_(False), self._authority(authority)]
        if active_only:
            clauses.append(c.enabled.is_(True))
        if scope_account_id is not None:
            clauses.append(c.account_id == scope_account_id)
        if target_uri is not None:
            clauses.extend([c.target_uri_hash == _hash(target_uri), c.target_uri == target_uri])
        async with self._transaction() as session:
            rows = await self._rows(
                session,
                select(*self._tasks.c)
                .where(*clauses)
                .order_by(
                    c.created_at.desc(),
                    c.task_id.desc(),
                ),
            )
            return [self._task(row) for row in rows]

    async def create_task(self, task: WatchTask, *, initial_run_id: Optional[str] = None) -> None:
        """Insert a Watch; the caller holds the target PathLock."""
        c = self._tasks.c
        async with self._transaction() as session:
            others = await self._rows(
                session,
                select(c.task_id, c.connector).where(
                    self._scope,
                    c.account_id == task.account_id,
                    c.deleted.is_(False),
                    c.target_uri_hash == _hash(task.to_uri),
                    c.target_uri == task.to_uri,
                ),
            )
            if others and not (
                is_connector(task.auth_state) and all(row["connector"] for row in others)
            ):
                raise ConflictError(target_conflict_message(task.to_uri), resource=task.to_uri)
            try:
                await session.execute(
                    insert(self._tasks).values(
                        service_name=self._service_name,
                        task_id=task.task_id,
                        account_id=task.account_id,
                        user_id=task.user_id,
                        original_role=task.original_role,
                        target_uri_hash=_hash(task.to_uri),
                        target_uri=task.to_uri,
                        enabled=task.is_active,
                        deleted=False,
                        watch_interval=task.watch_interval,
                        next_run_at=_epoch(task.next_execution_time),
                        current_run_id=initial_run_id,
                        run_started_at=db_now() if initial_run_id else None,
                        last_execution_time=_epoch(task.last_execution_time),
                        last_task_id=task.last_task_id,
                        last_status=task.last_status,
                        last_error=task.last_error,
                        payload=self._payload(task),
                        connector=is_connector(task.auth_state),
                        auth_state=self._cipher.seal(task.task_id, "auth_state", task.auth_state),
                        connector_states=self._cipher.seal(
                            task.task_id, "connector_states", task.connector_states
                        ),
                        created_at=_epoch(task.created_at),
                        updated_at=db_now(),
                    )
                )
            except IntegrityError as exc:
                raise ConflictError(
                    f"Watch '{task.task_id}' already exists",
                    resource=task.task_id,
                ) from exc

    async def patch_task(
        self,
        task_id: str,
        patch: WatchPatch,
        authority: WatchAuthority = WatchAuthority(),
        *,
        run_id: Optional[str] = None,
    ) -> Optional[WatchTask]:
        """Apply explicit fields. With *run_id*, only the occupying run may write."""
        values = patch.normalized()
        c = self._tasks.c
        where = [self._scope, c.task_id == task_id, self._authority(authority)]
        where.append(c.deleted.is_(False) if run_id is None else c.current_run_id == run_id)
        async with self._transaction() as session:
            rows = await self._rows(session, select(*self._tasks.c).where(*where).with_for_update())
            if not rows:
                if run_id is None:
                    exists = await self._rows(
                        session,
                        select(c.task_id).where(
                            self._scope,
                            c.task_id == task_id,
                            c.deleted.is_(False),
                        ),
                    )
                    if exists:
                        raise PermissionDeniedError(f"No permission to update Watch {task_id}")
                return None
            row = rows[0]
            sets: dict[str, Any] = {}
            payload = dict(row["payload"] or {})
            for name in PAYLOAD_FIELDS & values.keys():
                payload[name] = values[name]
            if payload != row["payload"]:
                sets["payload"] = payload
            for name in _SECRET_FIELDS:
                if name in values:
                    sets[name] = self._cipher.seal(task_id, name, values[name])
            if "auth_state" in values:
                sets["connector"] = is_connector(values["auth_state"])
            if "is_active" in values or "watch_interval" in values:
                enabled = bool(values.get("is_active", row["enabled"]))
                interval = float(values.get("watch_interval", row["watch_interval"]))
                base = row["last_execution_time"] or row["created_at"]
                next_run_at = row["next_run_at"]
                if "watch_interval" in values or next_run_at is None:
                    next_run_at = base + interval * 60
                sets.update(
                    enabled=enabled,
                    watch_interval=interval,
                    next_run_at=next_run_at if enabled else None,
                )
            if sets:
                sets["updated_at"] = db_now()
                await session.execute(
                    update(self._tasks)
                    .where(
                        self._scope,
                        c.task_id == task_id,
                    )
                    .values(**sets)
                )
            rows = await self._rows(
                session, select(*self._tasks.c).where(self._scope, c.task_id == task_id)
            )
            return self._task(rows[0])

    async def delete_task(self, task_id: str, authority: WatchAuthority) -> bool:
        """Tombstone a Watch; a row still occupied by a run is purged when it settles."""
        c = self._tasks.c
        async with self._transaction() as session:
            changed = (
                await session.execute(
                    update(self._tasks)
                    .where(
                        self._scope,
                        c.task_id == task_id,
                        c.deleted.is_(False),
                        self._authority(authority),
                    )
                    .values(deleted=True, enabled=False, next_run_at=None, updated_at=db_now())
                )
            ).rowcount
            if not changed:
                exists = await self._rows(
                    session,
                    select(c.task_id).where(
                        self._scope,
                        c.task_id == task_id,
                        c.deleted.is_(False),
                    ),
                )
                if exists:
                    raise PermissionDeniedError(f"No permission to delete Watch {task_id}")
                return False
            await self._purge(session, [task_id])
            return True

    async def delete_tasks(
        self, account_id: str, user_id: Optional[str] = None
    ) -> list[WatchState]:
        """Tombstone an identity's Watches and return those still occupied by a run."""
        c = self._tasks.c
        where = [self._scope, c.account_id == account_id]
        if user_id is not None:
            where.append(c.user_id == user_id)
        async with self._transaction() as session:
            await session.execute(
                update(self._tasks)
                .where(*where)
                .values(
                    deleted=True,
                    enabled=False,
                    next_run_at=None,
                    updated_at=db_now(),
                )
            )
            rows = await self._rows(
                session,
                self._select_state().where(
                    *where,
                    c.current_run_id.is_not(None),
                ),
            )
            await session.execute(delete(self._tasks).where(*where, c.current_run_id.is_(None)))
            return [self._state(row) for row in rows]

    async def _purge(self, session: Any, task_ids: Sequence[str]) -> None:
        c = self._tasks.c
        await session.execute(
            delete(self._tasks).where(
                self._scope,
                c.task_id.in_(list(task_ids)),
                c.deleted.is_(True),
                c.current_run_id.is_(None),
            )
        )

    # ── Run occupancy ───────────────────────────────────────────────────

    async def due(self, limit: int, *, offset: int = 0) -> list[WatchState]:
        c = self._tasks.c
        async with self._transaction() as session:
            rows = await self._rows(
                session,
                self._select_state()
                .where(
                    self._scope,
                    c.enabled.is_(True),
                    c.next_run_at <= db_now(),
                    c.deleted.is_(False),
                    c.current_run_id.is_(None),
                )
                .order_by(c.next_run_at, c.task_id)
                .offset(offset)
                .limit(limit),
            )
            return [self._state(row) for row in rows]

    async def running(self, *, after: str = "", limit: int = 100) -> list[WatchState]:
        c = self._tasks.c
        async with self._transaction() as session:
            rows = await self._rows(
                session,
                self._select_state()
                .where(
                    self._scope,
                    c.current_run_id.is_not(None),
                    c.task_id > after,
                )
                .order_by(c.task_id)
                .limit(limit),
            )
            return [self._state(row) for row in rows]

    async def begin_run(
        self,
        task_id: str,
        *,
        run_id: str,
        target_uri: Optional[str],
        manual: bool = False,
    ) -> Optional[WatchRun]:
        """Begin *run_id* on an idle Watch whose target is still *target_uri*.

        The caller holds that target's Tree PathLock, so no transfer of the
        target can interleave between this check and the commit.
        """
        if not run_id:
            raise ValueError("run_id is required")
        c = self._tasks.c
        where = [
            self._scope,
            c.task_id == task_id,
            c.deleted.is_(False),
            c.current_run_id.is_(None),
            c.target_uri_hash == _hash(target_uri),
        ]
        where.append(c.target_uri.is_(None) if target_uri is None else c.target_uri == target_uri)
        if not manual:
            where.extend([c.enabled.is_(True), c.next_run_at <= db_now()])
        async with self._transaction() as session:
            changed = (
                await session.execute(
                    update(self._tasks)
                    .where(*where)
                    .values(
                        current_run_id=run_id,
                        run_started_at=db_now(),
                        run_fenced=False,
                        updated_at=db_now(),
                    )
                )
            ).rowcount
            if not changed:
                return None
            rows = await self._rows(
                session, select(*self._tasks.c).where(self._scope, c.task_id == task_id)
            )
            return WatchRun(task_id=task_id, run_id=run_id, task=self._task(rows[0]))

    async def finish_run(self, task_id: str, run_id: str, result: WatchRunResult) -> bool:
        """Release occupancy and record the outcome iff *run_id* still occupies the row."""
        c = self._tasks.c
        now = db_now()
        if result.deactivate:
            next_run_at: Any = None
        else:
            next_run_at = case(
                (and_(c.enabled.is_(True), c.deleted.is_(False)), now + c.watch_interval * 60),
                else_=None,
            )
        values: dict[str, Any] = {
            "next_run_at": next_run_at,
            "last_execution_time": now,
            "last_task_id": result.execution_task_id,
            "last_status": result.status,
            "last_error": result.error,
            "current_run_id": None,
            "run_started_at": None,
            "run_fenced": False,
            "updated_at": now,
        }
        if result.deactivate:
            values["enabled"] = False
        if result.has_connector_states:
            values["connector_states"] = self._cipher.seal(
                task_id, "connector_states", result.connector_states
            )
        async with self._transaction() as session:
            changed = (
                await session.execute(
                    update(self._tasks)
                    .where(
                        self._scope,
                        c.task_id == task_id,
                        c.current_run_id == run_id,
                    )
                    .values(**values)
                )
            ).rowcount
            if changed:
                await self._purge(session, [task_id])
            return bool(changed)

    async def fence_run(self, task_id: str, run_id: str) -> bool:
        """Stop admitting Task creation for *run_id* while it keeps occupying the row."""
        c = self._tasks.c
        async with self._transaction() as session:
            await session.execute(
                update(self._tasks)
                .where(self._scope, c.task_id == task_id, c.current_run_id == run_id)
                .values(run_fenced=True)
            )
            rows = await self._rows(
                session,
                select(c.current_run_id).where(self._scope, c.task_id == task_id),
            )
            return bool(rows) and rows[0]["current_run_id"] == run_id

    async def deactivate_idle(self, task_id: str, *, target_uri: Optional[str], error: str) -> bool:
        """Disable an idle Watch that still targets *target_uri* without starting a run."""
        c = self._tasks.c
        now = db_now()
        where = [
            self._scope,
            c.task_id == task_id,
            c.deleted.is_(False),
            c.current_run_id.is_(None),
            c.target_uri_hash == _hash(target_uri),
        ]
        where.append(c.target_uri.is_(None) if target_uri is None else c.target_uri == target_uri)
        async with self._transaction() as session:
            changed = (
                await session.execute(
                    update(self._tasks)
                    .where(*where)
                    .values(
                        enabled=False,
                        next_run_at=None,
                        last_execution_time=now,
                        last_task_id=None,
                        last_status="failed",
                        last_error=error,
                        updated_at=now,
                    )
                )
            ).rowcount
            return bool(changed)

    async def has_running_overlap(self, account_id: str, uris: Sequence[str]) -> bool:
        """Whether a running Watch target equals, contains or lies under any of *uris*."""
        uris = sorted({uri.rstrip("/") for uri in uris if uri})
        if not uris:
            return False
        c = self._tasks.c
        ancestors = {
            uri[:i]
            for uri in uris
            for i, ch in enumerate(uri)
            if ch == "/" and i > len("viking://")
        }
        overlaps = [self._prefix(uri) for uri in uris]
        if ancestors:
            overlaps.append(
                and_(
                    c.target_uri_hash.in_(sorted(_hash(uri) for uri in ancestors)),
                    c.target_uri.in_(sorted(ancestors)),
                )
            )
        async with self._transaction() as session:
            rows = await self._rows(
                session,
                select(c.task_id)
                .where(
                    self._scope,
                    c.account_id == account_id,
                    c.current_run_id.is_not(None),
                    or_(*overlaps),
                )
                .limit(1),
            )
            return bool(rows)

    # ── Target transfers (caller holds the transfer's PathLock) ─────────

    async def _plan_move(
        self,
        session: Any,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]],
    ) -> list[tuple[Any, str]]:
        c = self._tasks.c
        old_prefix, new_prefix = from_uri.rstrip("/"), to_uri.rstrip("/")
        predicates = [
            self._scope,
            c.account_id == account_id,
            c.deleted.is_(False),
            self._prefix(old_prefix),
        ]
        if task_ids is not None:
            predicates.append(c.task_id.in_(task_ids))
        moving = await self._rows(
            session,
            select(*self._tasks.c).where(*predicates).order_by(c.task_id).with_for_update(),
        )
        if task_ids is not None and {row["task_id"] for row in moving} != set(task_ids):
            raise ConflictError(
                "Watch move source changed before the operation completed",
                resource=from_uri,
            )
        plan = [(row, new_prefix + row["target_uri"][len(old_prefix) :]) for row in moving]
        if not plan:
            return []
        moving_ids = {row["task_id"] for row, _ in plan}
        landing: dict[str, list[Any]] = {}
        for row, target in plan:
            landing.setdefault(target, []).append(row)
        staying = await self._rows(
            session,
            select(c.task_id, c.target_uri, c.connector)
            .where(
                self._scope,
                c.account_id == account_id,
                c.deleted.is_(False),
                c.target_uri_hash.in_(sorted(_hash(target) for target in landing)),
            )
            .with_for_update(),
        )
        for row in staying:
            if row["task_id"] not in moving_ids and row["target_uri"] in landing:
                landing[row["target_uri"]].append(row)
        for target, occupants in landing.items():
            if len(occupants) > 1 and not all(row["connector"] for row in occupants):
                raise ConflictError(target_conflict_message(target), resource=target)
        return plan

    async def move(
        self,
        *,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]] = None,
    ) -> WatchMoveResult:
        requested_ids = None if task_ids is None else tuple(dict.fromkeys(task_ids))
        if requested_ids == ():
            return WatchMoveResult(account_id, from_uri.rstrip("/"), to_uri.rstrip("/"), ())
        c = self._tasks.c
        async with self._transaction() as session:
            plan = await self._plan_move(session, account_id, from_uri, to_uri, requested_ids)
            for row, target in plan:
                payload = dict(row["payload"] or {})
                if payload.get("parent_uri") is not None and payload["parent_uri"] == parent_uri(
                    row["target_uri"]
                ):
                    payload["parent_uri"] = parent_uri(target)
                await session.execute(
                    update(self._tasks)
                    .where(
                        self._scope,
                        c.task_id == row["task_id"],
                    )
                    .values(
                        target_uri=target,
                        target_uri_hash=_hash(target),
                        payload=payload,
                        updated_at=db_now(),
                    )
                )
            return WatchMoveResult(
                account_id,
                from_uri.rstrip("/"),
                to_uri.rstrip("/"),
                tuple(row["task_id"] for row, _ in plan),
            )

    async def deactivate_under_uri(self, account_id: str, uri: str) -> list[WatchTask]:
        c = self._tasks.c
        where = [self._scope, c.account_id == account_id, c.deleted.is_(False), self._prefix(uri)]
        async with self._transaction() as session:
            await session.execute(
                update(self._tasks)
                .where(*where)
                .values(
                    enabled=False,
                    next_run_at=None,
                    updated_at=db_now(),
                )
            )
            rows = await self._rows(session, select(*self._tasks.c).where(*where))
            return [self._task(row) for row in rows]
