# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Relational MySQL implementation of :class:`TaskWorkStore`."""

from __future__ import annotations

import hashlib
import os
import ssl as ssl_module
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Collection, Dict, Iterable, Optional

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from openviking.service.task_domain import (
    ACTIVE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    DeletedTask,
    DeleteExpiredResult,
    TaskPatch,
    TaskRecord,
    TaskRetentionPolicy,
    TaskStatus,
    WorkRecord,
    WorkRecoveryMode,
    WorkState,
    WorkSummary,
    append_task_events,
    finalize_task,
    processing_seconds,
    sanitize_error,
    summarize_works,
)
from openviking.service.task_events import append_task_event
from openviking.service.task_store import (
    DeleteTasksResult,
    StoreOperationCode,
    StoreOperationResult,
    TaskAlreadyActive,
    TaskDeletionBlocker,
    TaskStatsSnapshot,
    TaskWorkStore,
    _deep_merge,
)
from openviking.service.task_store_mysql_schema import (
    TaskStoreModels,
    make_task_store_models,
    validate_table_prefix,
)
from openviking.storage.mysql_runtime import (
    MySQLRuntime,
    get_shared_mysql_runtime,
    mysql_runtime_store,
    wait_mysql_cleanup,
)
from openviking.storage.mysql_session import MySQLAsyncSession
from openviking_cli.utils.config.config_utils import DEFAULT_SERVICE_NAME, normalize_service_name


def _business_key(account_id: str, user_id: str, task_type: str, resource_id: Optional[str]) -> str:
    """Hash the guarded business key into a fixed-width unique-index value."""
    required = (account_id, user_id, task_type)
    encoded = [len(value.encode()).to_bytes(2, "big") + value.encode() for value in required]
    if resource_id is None:
        encoded.append(b"\x00")
    else:
        value = resource_id.encode()
        encoded.append(b"\x01" + len(value).to_bytes(2, "big") + value)
    return hashlib.sha256(b"".join(encoded)).hexdigest()


def _resource_id_hash(resource_id: Optional[str]) -> str | None:
    return hashlib.sha256(resource_id.encode()).hexdigest() if resource_id is not None else None


def _json_copy(value: Any) -> Any:
    return deepcopy(value) if value is not None else None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _not_found_result() -> StoreOperationResult:
    return StoreOperationResult.rejected(StoreOperationCode.NOT_FOUND)


def _inactive_result(status: str, message: str) -> StoreOperationResult:
    if status in {item.value for item in TERMINAL_TASK_STATUSES}:
        return StoreOperationResult.rejected(
            StoreOperationCode.ALREADY_TERMINAL,
            f"{message}: task is already {status}",
        )
    return StoreOperationResult.rejected(
        StoreOperationCode.INVALID_STATE,
        f"{message}: task is {status}",
    )


@mysql_runtime_store
class MySQLTaskWorkStore(TaskWorkStore):
    """Durable Task/Work storage with transaction-owned state transitions."""

    def __init__(
        self,
        *,
        params: dict[str, object] | None = None,
        service_name: str | None = None,
        runtime: MySQLRuntime | None = None,
    ) -> None:
        values = dict(params or {})
        param_service_name = values.pop("service_name", DEFAULT_SERVICE_NAME)
        raw_service_name = service_name if service_name is not None else param_service_name
        self._service_name = normalize_service_name(raw_service_name)
        table_prefix = validate_table_prefix(values.pop("table", "ov_tasks"))
        self._models: TaskStoreModels = make_task_store_models(table_prefix)
        self._resource_id = os.environ.get("OV_RESOURCE_ID", "").strip()
        self._url, self._engine_options = self._connection_options(values)
        self._mysql_runtime = runtime or get_shared_mysql_runtime()
        self._mysql_lease = False
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    @staticmethod
    def _connection_options(values: dict[str, object]) -> tuple[URL, dict[str, object]]:
        allowed = {
            "user",
            "password",
            "database",
            "host",
            "port",
            "maxsize",
            "connect_timeout",
            "ssl",
            "unix_socket",
        }
        unknown = values.keys() - allowed
        if unknown:
            raise ValueError(f"Unknown MySQL task store parameters: {', '.join(sorted(unknown))}")
        missing = [name for name in ("user", "password", "database") if name not in values]
        if missing:
            raise ValueError(f"MySQL task store requires: {', '.join(missing)}")

        connect_args: dict[str, object] = {
            "connect_timeout": values.get("connect_timeout", 10),
        }
        if socket := values.get("unix_socket"):
            connect_args["unix_socket"] = socket
        ssl = values.get("ssl")
        if isinstance(ssl, dict):
            unknown_ssl = ssl.keys() - {"ca", "cert", "key", "check_hostname"}
            if unknown_ssl:
                raise ValueError(f"Unknown MySQL TLS options: {sorted(unknown_ssl)}")
            context = ssl_module.create_default_context(cafile=ssl.get("ca"))
            context.check_hostname = bool(ssl.get("check_hostname", True))
            if ssl.get("cert"):
                context.load_cert_chain(ssl["cert"], ssl.get("key"))
            connect_args["ssl"] = context
        elif ssl is True:
            connect_args["ssl"] = ssl_module.create_default_context()
        elif ssl not in (None, False):
            raise ValueError("ssl must be a boolean or TLS options mapping")

        url = URL.create(
            "mysql+aiomysql",
            username=str(values["user"]),
            password=str(values["password"]),
            host=str(values.get("host", "127.0.0.1")),
            port=int(str(values.get("port", 3306))),
            database=str(values["database"]),
            query={"charset": "utf8mb4"},
        )
        return url, {
            "pool_size": int(values.get("maxsize", 10)),
            "max_overflow": 0,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "connect_args": connect_args,
            "isolation_level": "READ COMMITTED",
        }

    @property
    def work_recovery_mode(self) -> WorkRecoveryMode:
        return WorkRecoveryMode.DURABLE_STORE

    async def load(self) -> None:
        """Connect and verify that the manually provisioned schema is usable."""
        if self._engine is not None:
            return
        await self._mysql_runtime.acquire()
        self._mysql_lease = True

        async def cleanup_failed_load(engine: AsyncEngine | None = None) -> None:
            if engine is not None:
                await engine.dispose()
            if self._mysql_lease:
                self._mysql_lease = False
                await self._mysql_runtime.release()

        try:
            engine = create_async_engine(self._url, **self._engine_options)
            sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
                engine, class_=MySQLAsyncSession, expire_on_commit=False
            )
            async with sessions() as session:
                for model in (self._models.Task, self._models.Work, self._models.Event):
                    await session.execute(select(*model.__table__.columns).limit(1))
        except ImportError:
            await wait_mysql_cleanup(cleanup_failed_load())
            raise RuntimeError("MySQL task storage requires openviking[mysql]") from None
        except DBAPIError as exc:
            await wait_mysql_cleanup(cleanup_failed_load(engine if "engine" in locals() else None))
            message = str(exc).lower()
            if any(
                marker in message
                for marker in ("unknown column", "doesn't exist", "does not exist")
            ):
                raise RuntimeError(
                    "MySQL task schema is missing or incompatible; provision the configured "
                    "Task/Work tables before starting OpenViking"
                ) from exc
            raise
        except BaseException:
            await wait_mysql_cleanup(cleanup_failed_load(engine if "engine" in locals() else None))
            raise
        self._engine = engine
        self._sessions = sessions

    async def close(self) -> None:
        async def cleanup() -> None:
            if self._engine is not None:
                await self._engine.dispose()
            self._engine = None
            self._sessions = None
            if self._mysql_lease:
                self._mysql_lease = False
                await self._mysql_runtime.release()

        await wait_mysql_cleanup(cleanup())

    @property
    def _session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._sessions is None:
            raise RuntimeError("MySQLTaskWorkStore.load() must be called before use")
        return self._sessions

    @staticmethod
    def _require_owner(account_id: Optional[str], user_id: Optional[str]) -> tuple[str, str]:
        if not account_id or not user_id:
            raise ValueError("MySQLTaskWorkStore requires account_id and user_id")
        return account_id, user_id

    def _task_query(
        self,
        Task: Any,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ):
        predicates = [Task.service_name == self._service_name, Task.task_id == task_id]
        if account_id is not None:
            predicates.append(Task.account_id == account_id)
        if user_id is not None:
            predicates.append(Task.user_id == user_id)
        return predicates

    def _work_query(
        self,
        Work: Any,
        task_id: str,
        work_id: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ):
        predicates = [Work.service_name == self._service_name, Work.task_id == task_id]
        if work_id is not None:
            predicates.append(Work.work_id == work_id)
        if account_id is not None:
            predicates.append(Work.account_id == account_id)
        if user_id is not None:
            predicates.append(Work.user_id == user_id)
        return predicates

    def _event_query(self, Event: Any, task_id: str):
        return Event.service_name == self._service_name, Event.task_id == task_id

    @staticmethod
    def _task_from_row(
        row: Any,
        works: Iterable[WorkRecord] = (),
        execution_events: Optional[dict] = None,
    ) -> TaskRecord:
        task = TaskRecord(
            task_id=row.task_id,
            task_type=row.task_type,
            status=TaskStatus(row.status),
            created_at=float(row.created_at),
            updated_at=float(row.updated_at),
            resource_id=row.resource_id,
            account_id=row.account_id,
            user_id=row.user_id,
            meta=_json_copy(row.meta) or {},
            stage=row.stage,
            result=_json_copy(row.result),
            error=row.error,
            auth=_json_copy(row.auth) or {},
            execution_events=_json_copy(execution_events),
            processing_seconds=(
                processing_seconds(works)
                if row.status in {item.value for item in ACTIVE_TASK_STATUSES}
                else row.processing_seconds
            ),
        )
        return task

    @staticmethod
    def _work_from_row(row: Any) -> WorkRecord:
        return WorkRecord(
            work_id=row.work_id,
            task_id=row.task_id,
            queue_name=row.queue_name,
            account_id=row.account_id,
            user_id=row.user_id,
            state=WorkState(row.state),
            requeue_count=int(row.requeue_count),
            error=row.error,
            errors=list(row.errors or ()),
            context_count=int(row.context_count),
            enqueued_at=float(row.enqueued_at),
            started_at=float(row.started_at) if row.started_at is not None else None,
            finished_at=float(row.finished_at) if row.finished_at is not None else None,
            updated_at=float(row.updated_at),
        )

    async def _load_task(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        account_id: Optional[str],
        user_id: Optional[str],
        lock: bool = False,
    ) -> Any:
        Task = self._models.Task
        statement = select(Task).where(*self._task_query(Task, task_id, account_id, user_id))
        if lock:
            statement = statement.with_for_update()
        return await session.scalar(statement)

    async def _lock_task(
        self, session: AsyncSession, task_id: str, *, account_id: str, user_id: str
    ) -> bool:
        """Serialize Work closure with registration and deletion, Task before Work."""
        Task = self._models.Task
        return (
            await session.scalar(
                select(Task.task_id)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .with_for_update()
            )
        ) is not None

    async def _task_exists(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        Task = self._models.Task
        return bool(
            await session.scalar(
                select(exists().where(*self._task_query(Task, task_id, account_id, user_id)))
            )
        )

    async def _has_open_work(self, session: AsyncSession, task_id: str) -> bool:
        Work = self._models.Work
        return bool(
            await session.scalar(
                select(
                    exists().where(
                        *self._work_query(Work, task_id, None, None, None),
                        Work.state.not_in([WorkState.DONE.value, WorkState.FAILED.value]),
                    )
                )
            )
        )

    async def _works(
        self,
        session: AsyncSession,
        task_id: str,
        work_ids: Optional[Collection[str]] = None,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> list[WorkRecord]:
        Work = self._models.Work
        statement = select(Work).where(*self._work_query(Work, task_id, None, account_id, user_id))
        if work_ids is not None:
            ids = set(work_ids)
            if not ids:
                return []
            statement = statement.where(Work.work_id.in_(ids))
        rows = (await session.scalars(statement)).all()
        return [self._work_from_row(row) for row in rows]

    async def _event_history(self, session: AsyncSession, task_id: str) -> Optional[dict]:
        Event = self._models.Event
        rows = (
            await session.scalars(
                select(Event).where(*self._event_query(Event, task_id)).order_by(Event.seq)
            )
        ).all()
        if not rows:
            return None
        latest = rows[-1]
        return {
            "items": [
                {
                    "seq": int(row.seq),
                    "recorded_at": row.recorded_at,
                    "kind": row.kind,
                    "status": row.status,
                    "stage": row.stage,
                    "operation": row.operation,
                    "error": row.error,
                }
                for row in rows
            ],
            "dropped_count": int(latest.dropped_count),
            "started_mid_task": bool(latest.started_mid_task),
        }

    async def _store_event_history(
        self,
        session: AsyncSession,
        task_id: str,
        history: Optional[dict],
    ) -> None:
        if history is None:
            return
        Event = self._models.Event
        items = history["items"]
        if not items:
            return
        seqs = [int(item["seq"]) for item in items]
        existing = set(
            await session.scalars(
                select(Event.seq).where(
                    *self._event_query(Event, task_id),
                    Event.seq.in_(seqs),
                )
            )
        )
        metadata = {
            "dropped_count": int(history["dropped_count"]),
            "started_mid_task": bool(history["started_mid_task"]),
        }
        await session.execute(
            update(Event).where(*self._event_query(Event, task_id)).values(**metadata)
        )
        for item in items:
            if int(item["seq"]) in existing:
                continue
            session.add(
                Event(
                    service_name=self._service_name,
                    task_id=task_id,
                    seq=int(item["seq"]),
                    recorded_at=item["recorded_at"],
                    kind=item["kind"],
                    status=item["status"],
                    stage=item["stage"],
                    operation=item["operation"],
                    error=item["error"],
                    **metadata,
                )
            )
        await session.execute(
            delete(Event).where(*self._event_query(Event, task_id), Event.seq < min(seqs))
        )

    async def _lock_work(self, session: AsyncSession, task_id: str, work_id: str) -> Any:
        Work = self._models.Work
        return await session.scalar(
            select(Work)
            .where(*self._work_query(Work, task_id, work_id, None, None))
            .with_for_update()
        )

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            async with session.begin():
                yield session

    async def create(self, task: TaskRecord, *, reject_if_active: bool = False) -> TaskRecord:
        account_id, user_id = self._require_owner(task.account_id, task.user_id)
        Task = self._models.Task
        now = time.time()
        stored = deepcopy(task)
        if stored.created_at <= 0:
            stored.created_at = now
        if stored.updated_at <= 0:
            stored.updated_at = stored.created_at
        stored.account_id = account_id
        stored.user_id = user_id
        stored._extra_fields = {}
        append_task_events(stored, None)
        row = Task(
            service_name=self._service_name,
            task_id=stored.task_id,
            task_type=stored.task_type,
            status=stored.status.value,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            resource_id=stored.resource_id,
            resource_id_hash=_resource_id_hash(stored.resource_id),
            account_id=account_id,
            user_id=user_id,
            meta=_json_copy(stored.meta),
            stage=stored.stage,
            result=_json_copy(stored.result),
            error=stored.error,
            auth=_json_copy(stored.auth),
            processing_seconds=stored.processing_seconds,
            reject_if_active=reject_if_active,
            is_internal=bool(stored.meta.get("internal")),
            active_business_key=(
                _business_key(account_id, user_id, stored.task_type, stored.resource_id)
                if reject_if_active and stored.status in ACTIVE_TASK_STATUSES
                else None
            ),
        )
        try:
            async with self._transaction() as session:
                existing = await self._load_task(
                    session,
                    stored.task_id,
                    account_id=None,
                    user_id=None,
                    lock=True,
                )
                if existing is not None:
                    if existing.account_id != account_id or existing.user_id != user_id:
                        raise ValueError(
                            f"Task id already belongs to another owner: {stored.task_id}"
                        )
                    return self._task_from_row(
                        existing,
                        execution_events=await self._event_history(session, stored.task_id),
                    )
                session.add(row)
                await self._store_event_history(session, stored.task_id, stored.execution_events)
        except IntegrityError as exc:
            if not self._is_duplicate_error(exc):
                raise
            async with self._session_factory() as session:
                existing = await self._load_task(
                    session,
                    stored.task_id,
                    account_id=None,
                    user_id=None,
                )
                if existing is not None:
                    if existing.account_id != account_id or existing.user_id != user_id:
                        raise ValueError(
                            f"Task id already belongs to another owner: {stored.task_id}"
                        )
                    return self._task_from_row(
                        existing,
                        execution_events=await self._event_history(session, stored.task_id),
                    )
                if reject_if_active:
                    raise TaskAlreadyActive(stored.task_type, stored.resource_id) from exc
            raise
        return stored

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        original = getattr(exc, "orig", None)
        args = getattr(original, "args", ())
        return bool(args and args[0] == 1062) or "duplicate" in str(exc).lower()

    async def _populate_processing_seconds(
        self, session: AsyncSession, tasks: list[TaskRecord]
    ) -> None:
        """Project active durations from batched, interval-only Work reads."""
        active = [task for task in tasks if task.status in ACTIVE_TASK_STATUSES]
        Work = self._models.Work
        now = time.time()
        for offset in range(0, len(active), 200):
            batch = active[offset : offset + 200]
            intervals: dict[str, list[WorkRecord]] = {task.task_id: [] for task in batch}
            rows = await session.execute(
                select(Work.task_id, Work.started_at, Work.finished_at).where(
                    Work.service_name == self._service_name,
                    Work.task_id.in_(intervals),
                    Work.started_at.is_not(None),
                )
            )
            for row in rows:
                intervals[row.task_id].append(
                    WorkRecord(
                        task_id=row.task_id,
                        work_id="",
                        queue_name="",
                        started_at=float(row.started_at),
                        finished_at=(
                            float(row.finished_at) if row.finished_at is not None else None
                        ),
                    )
                )
            for task in batch:
                task.processing_seconds = processing_seconds(intervals[task.task_id], now=now)

    async def get_task(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        async with self._session_factory() as session:
            row = await self._load_task(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if row is None:
                return None
            task = self._task_from_row(row)
            await self._populate_processing_seconds(session, [task])
            return task

    async def get_task_with_events(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        async with self._session_factory() as session:
            row = await self._load_task(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if row is None:
                return None
            task = self._task_from_row(
                row, execution_events=await self._event_history(session, task_id)
            )
            await self._populate_processing_seconds(session, [task])
            return task

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
        before: tuple[float, str] | None = None,
        include_internal: bool = True,
        q: Optional[str] = None,
    ) -> list[TaskRecord]:
        if limit is not None and limit <= 0:
            return []

        Task = self._models.Task

        def statement_for(cursor: tuple[float, str] | None, batch_limit: int):
            predicates = [Task.service_name == self._service_name]
            if account_id is not None:
                predicates.append(Task.account_id == account_id)
            if user_id is not None:
                predicates.append(Task.user_id == user_id)
            if task_type is not None:
                predicates.append(Task.task_type == task_type)
            if status is not None:
                predicates.append(Task.status == status)
            if resource_id is not None:
                predicates.append(Task.resource_id_hash == _resource_id_hash(resource_id))
                predicates.append(Task.resource_id == resource_id)
            if not include_internal:
                predicates.append(Task.is_internal.is_(False))
            if cursor is not None:
                predicates.append(
                    or_(
                        Task.created_at < cursor[0],
                        and_(Task.created_at == cursor[0], Task.task_id < cursor[1]),
                    )
                )
            return (
                select(Task)
                .where(*predicates)
                .order_by(Task.created_at.desc(), Task.task_id.desc())
                .limit(batch_limit)
            )

        async with self._session_factory() as session:
            if q is None:
                batch_size = limit if limit is not None else 200
                cursor = before
                result: list[TaskRecord] = []
                while True:
                    rows = (await session.scalars(statement_for(cursor, batch_size))).all()
                    tasks = [self._task_from_row(row) for row in rows]
                    await self._populate_processing_seconds(session, tasks)
                    result.extend(tasks)
                    if limit is not None or len(rows) < batch_size:
                        return result
                    cursor = (float(rows[-1].created_at), rows[-1].task_id)

            from openviking.service.task_pagination import matches

            batch_size = max(100, min(1000, (limit or 100) * 2))
            cursor = before
            result = []
            while True:
                rows = (await session.scalars(statement_for(cursor, batch_size))).all()
                if not rows:
                    break
                for row in rows:
                    task = self._task_from_row(row)
                    if matches(
                        task.to_dict(),
                        task_type=task_type,
                        status=status,
                        resource_id=resource_id,
                        include_internal=include_internal,
                        q=q,
                    ):
                        result.append(task)
                        if limit is not None and len(result) >= limit:
                            await self._populate_processing_seconds(session, result)
                            return result
                cursor = (float(rows[-1].created_at), rows[-1].task_id)
                if len(rows) < batch_size:
                    break
            await self._populate_processing_seconds(session, result)
            return result

    def snapshot_task_stats(self) -> TaskStatsSnapshot:
        """Return no stale process-local snapshot; callers use durable list APIs."""
        return {}

    async def _delete_task_records(self, session: AsyncSession, task_ids: Collection[str]) -> None:
        """Delete locked Tasks and their dependent records in the current transaction."""
        if not task_ids:
            return
        for model in (self._models.Work, self._models.Event, self._models.Task):
            await session.execute(
                delete(model).where(
                    model.service_name == self._service_name,
                    model.task_id.in_(task_ids),
                )
            )

    async def delete_tasks(
        self,
        task_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: Optional[str] = None,
        allow_active_types: Collection[str] = (),
    ) -> DeleteTasksResult:
        if not account_id:
            raise ValueError("Task deletion requires a non-empty account_id")
        if task_id is not None and not user_id:
            raise ValueError("Deleting one Task requires user_id")
        Task = self._models.Task
        async with self._transaction() as session:
            predicates = [Task.service_name == self._service_name, Task.account_id == account_id]
            if task_id is not None:
                predicates.append(Task.task_id == task_id)
            if user_id is not None:
                predicates.append(Task.user_id == user_id)
            rows = (
                await session.scalars(
                    select(Task).where(*predicates).order_by(Task.task_id).with_for_update()
                )
            ).all()
            allowed = set(allow_active_types)
            task_ids = [row.task_id for row in rows]
            open_task_ids = set()
            if task_ids:
                Work = self._models.Work
                open_task_ids = set(
                    await session.scalars(
                        select(Work.task_id)
                        .where(
                            Work.service_name == self._service_name,
                            Work.task_id.in_(task_ids),
                            Work.state.not_in([WorkState.DONE.value, WorkState.FAILED.value]),
                        )
                        .distinct()
                    )
                )
            blockers = []
            for row in rows:
                if row.task_type in allowed:
                    continue
                if row.status in {status.value for status in ACTIVE_TASK_STATUSES}:
                    code = StoreOperationCode.ACTIVE_TASK
                    message = f"Cannot delete active task record: {row.task_id}({row.task_type})"
                elif row.task_id in open_task_ids:
                    code = StoreOperationCode.OPEN_WORK
                    message = f"Cannot delete active task record: {row.task_id}"
                else:
                    continue
                blockers.append(
                    TaskDeletionBlocker(
                        row.task_id, row.account_id, row.user_id, row.task_type, code, message
                    )
                )
            if blockers:
                active = [item for item in blockers if item.code is StoreOperationCode.ACTIVE_TASK]
                if active:
                    code = StoreOperationCode.ACTIVE_TASK
                    message = "Cannot delete active task records: " + ", ".join(
                        f"{item.task_id}({item.task_type})" for item in active
                    )
                elif len(blockers) == 1:
                    code, message = blockers[0].code, blockers[0].message
                else:
                    code = StoreOperationCode.OPEN_WORK
                    message = "Cannot delete active task records: " + ", ".join(
                        item.task_id for item in blockers
                    )
                return DeleteTasksResult(
                    result=StoreOperationResult.rejected(code, message),
                    blockers=tuple(blockers),
                )
            if task_id is not None and not rows:
                return DeleteTasksResult(
                    result=StoreOperationResult.noop(StoreOperationCode.NOT_FOUND)
                )
            deleted = tuple(
                DeletedTask(row.task_id, row.account_id, row.user_id)
                for row in sorted(rows, key=lambda row: (row.account_id, row.user_id, row.task_id))
            )
            await self._delete_task_records(session, task_ids)
            return DeleteTasksResult(deleted)

    async def delete_expired(
        self, policy: TaskRetentionPolicy, *, now: float, limit: int
    ) -> DeleteExpiredResult:
        if limit <= 0:
            return DeleteExpiredResult()
        Task = self._models.Task
        completed_cutoff = now - policy.completed_seconds
        failed_cutoff = now - policy.failed_seconds
        statement = (
            select(Task)
            .where(
                Task.service_name == self._service_name,
                or_(
                    and_(
                        Task.status.in_([TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]),
                        Task.updated_at < completed_cutoff,
                    ),
                    and_(
                        Task.status == TaskStatus.FAILED.value,
                        Task.updated_at < failed_cutoff,
                    ),
                ),
            )
            .order_by(Task.updated_at, Task.task_id)
            .limit(limit)
        )
        async with self._transaction() as session:
            rows = (await session.scalars(statement.with_for_update())).all()
            deleted: list[DeletedTask] = []
            task_ids = [row.task_id for row in rows]
            open_task_ids = set()
            if task_ids:
                Work = self._models.Work
                open_task_ids = set(
                    await session.scalars(
                        select(Work.task_id)
                        .where(
                            Work.service_name == self._service_name,
                            Work.task_id.in_(task_ids),
                            Work.state.not_in([WorkState.DONE.value, WorkState.FAILED.value]),
                        )
                        .distinct()
                    )
                )
            for row in rows:
                if row.task_id in open_task_ids:
                    continue
                deleted.append(DeletedTask(row.task_id, row.account_id, row.user_id))
            await self._delete_task_records(session, [task.task_id for task in deleted])
            return DeleteExpiredResult(
                tuple(deleted),
                scanned=len(rows),
                scan_complete=len(rows) < limit,
            )

    async def _finalize_if_closed(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[TaskRecord]:
        """Check for open Work under the Task lock held before any Work mutation."""
        row = await self._load_task(
            session,
            task_id,
            account_id=account_id,
            user_id=user_id,
            lock=True,
        )
        if row is None:
            return None
        if await self._has_open_work(session, task_id):
            return None
        works = await self._works(
            session,
            task_id,
            account_id=account_id,
            user_id=user_id,
        )
        previous = self._task_from_row(
            row,
            works,
            execution_events=await self._event_history(session, task_id),
        )
        task = deepcopy(previous)
        if not finalize_task(task, works):
            return previous

        task.updated_at = TaskRecord.next_updated_at(previous.updated_at)
        append_task_events(task, previous)
        Task = self._models.Task
        await session.execute(
            update(Task)
            .where(*self._task_query(Task, task_id, account_id, user_id))
            .values(
                status=task.status.value,
                updated_at=task.updated_at,
                stage=task.stage,
                result=_json_copy(task.result),
                error=task.error,
                auth=_json_copy(task.auth),
                processing_seconds=task.processing_seconds,
                active_business_key=None,
            )
        )
        await self._store_event_history(session, task_id, task.execution_events)
        return task

    async def start_task(
        self,
        task_id: str,
        *,
        stage: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        Task = self._models.Task
        async with self._transaction() as session:
            row = await session.execute(
                select(Task.status, Task.stage, Task.updated_at)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .with_for_update()
            )
            row = row.one_or_none()
            if row is None:
                return _not_found_result()
            if row.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
                return _inactive_result(row.status, "Task cannot be started")

            previous = TaskRecord(
                task_id=task_id,
                task_type="",
                status=TaskStatus(row.status),
                stage=row.stage,
                execution_events=await self._event_history(session, task_id),
            )
            task = deepcopy(previous)
            task.status = TaskStatus.RUNNING
            if stage is not None:
                task.stage = stage
            append_task_events(task, previous)
            await session.execute(
                update(Task)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .values(
                    status=task.status.value,
                    stage=task.stage,
                    updated_at=TaskRecord.next_updated_at(row.updated_at),
                )
            )
            await self._store_event_history(session, task_id, task.execution_events)
            return StoreOperationResult.applied()

    async def patch_task(
        self,
        task_id: str,
        patch: TaskPatch,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        Task = self._models.Task
        async with self._transaction() as session:
            selected = [Task.status, Task.updated_at]
            if "stage" in patch.clear_fields or patch.stage is not None:
                selected.append(Task.stage)
            if "meta" not in patch.clear_fields and patch.meta is not None:
                selected.append(Task.meta)
            if "auth" not in patch.clear_fields and (
                patch.auth is not None or patch.add_enqueue_complete_work_ids
            ):
                selected.append(Task.auth)
            result = await session.execute(
                select(*selected)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .with_for_update()
            )
            row = result.one_or_none()
            if row is None:
                return _not_found_result()
            if row.status not in {item.value for item in ACTIVE_TASK_STATUSES}:
                return _inactive_result(row.status, "Task cannot be patched")
            if patch.is_empty():
                return StoreOperationResult.noop()

            values: dict[str, Any] = {
                "updated_at": TaskRecord.next_updated_at(row.updated_at),
            }
            if "result" in patch.clear_fields or patch.result is not None:
                values["result"] = (
                    None if "result" in patch.clear_fields else _json_copy(patch.result)
                )
            if "stage" in patch.clear_fields or patch.stage is not None:
                stage = None if "stage" in patch.clear_fields else patch.stage
                values["stage"] = stage
                if stage != row.stage:
                    previous = TaskRecord(
                        task_id=task_id,
                        task_type="",
                        status=TaskStatus(row.status),
                        stage=row.stage,
                        execution_events=await self._event_history(session, task_id),
                    )
                    task = deepcopy(previous)
                    task.stage = stage
                    append_task_events(task, previous)
                    await self._store_event_history(session, task_id, task.execution_events)
            if "meta" in patch.clear_fields or patch.meta is not None:
                meta = {}
                if "meta" not in patch.clear_fields:
                    meta = _json_copy(row.meta) or {}
                    _deep_merge(meta, _json_copy(patch.meta))
                values["meta"] = meta
                values["is_internal"] = bool(meta.get("internal"))
            if (
                "auth" in patch.clear_fields
                or patch.auth is not None
                or patch.add_enqueue_complete_work_ids
            ):
                auth = {} if "auth" in patch.clear_fields else (_json_copy(row.auth) or {})
                if patch.auth is not None:
                    _deep_merge(auth, _json_copy(patch.auth))
                if patch.add_enqueue_complete_work_ids:
                    completed = set(auth.get("enqueue_complete_work_ids") or ())
                    completed.update(patch.add_enqueue_complete_work_ids)
                    auth["enqueue_complete_work_ids"] = sorted(completed)
                values["auth"] = auth
            await session.execute(
                update(Task)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .values(**values)
            )
            return StoreOperationResult.applied()

    async def record_event(
        self,
        task_id: str,
        kind: str,
        *,
        operation: Optional[str],
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        Task = self._models.Task
        async with self._transaction() as session:
            query_result = await session.execute(
                select(
                    Task.status,
                    Task.stage,
                    Task.updated_at,
                )
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .with_for_update()
            )
            row = query_result.one_or_none()
            if row is None:
                return _not_found_result()
            if row.status not in {item.value for item in ACTIVE_TASK_STATUSES}:
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {row.status}",
                )
            events = append_task_event(
                await self._event_history(session, task_id),
                kind=kind,
                status=row.status,
                stage=sanitize_error(row.stage) if row.stage else None,
                operation=operation,
            )
            await session.execute(
                update(Task)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .values(
                    updated_at=TaskRecord.next_updated_at(row.updated_at),
                )
            )
            await self._store_event_history(session, task_id, events)
            return StoreOperationResult.applied()

    async def record_outcome(
        self,
        task_id: str,
        *,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        resource_id: Optional[str],
        cancelled: bool,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        Task = self._models.Task
        async with self._transaction() as session:
            query_result = await session.execute(
                select(
                    Task.status,
                    Task.stage,
                    Task.error,
                    Task.resource_id,
                    Task.auth,
                    Task.updated_at,
                    Task.task_type,
                    Task.account_id,
                    Task.user_id,
                    Task.reject_if_active,
                )
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .with_for_update()
            )
            row = query_result.one_or_none()
            if row is None:
                return _not_found_result()
            if row.status not in {item.value for item in ACTIVE_TASK_STATUSES}:
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {row.status}",
                )

            values: dict[str, Any] = {}
            if result is not None:
                values["result"] = deepcopy(result)
            if resource_id is not None:
                values["resource_id"] = resource_id
                values["resource_id_hash"] = _resource_id_hash(resource_id)
                values["active_business_key"] = (
                    _business_key(
                        row.account_id,
                        row.user_id,
                        row.task_type,
                        resource_id,
                    )
                    if row.reject_if_active
                    else None
                )
            next_error = row.error
            if error and next_error is None:
                next_error = sanitize_error(error)
                values["error"] = next_error

            next_status = TaskStatus.CANCELLING if cancelled else TaskStatus(row.status)
            events = None
            if cancelled or (error and row.error is None):
                previous = TaskRecord(
                    task_id=task_id,
                    task_type=row.task_type,
                    status=TaskStatus(row.status),
                    stage=row.stage,
                    error=row.error,
                    execution_events=await self._event_history(session, task_id),
                )
                task = deepcopy(previous)
                task.status = next_status
                task.error = next_error
                append_task_events(task, previous)
                if cancelled:
                    values["status"] = task.status.value
                events = task.execution_events

            markers = (row.auth or {}).get("enqueue_complete_work_ids")
            auth = _json_copy(row.auth) or {}
            if markers:
                auth["enqueue_complete_work_ids"] = deepcopy(markers)
            else:
                auth.pop("enqueue_complete_work_ids", None)
            values["auth"] = auth
            await session.execute(
                update(Task)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .values(
                    **values,
                    updated_at=TaskRecord.next_updated_at(row.updated_at),
                )
            )
            await self._store_event_history(session, task_id, events)
            finalized = await self._finalize_if_closed(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if finalized is not None:
                return StoreOperationResult.applied()
            return StoreOperationResult.applied()

    async def request_cancellation(
        self,
        task_id: str,
        *,
        cancellable_types: Collection[str],
        allow_terminal: bool,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        Task = self._models.Task
        async with self._transaction() as session:
            result = await session.execute(
                select(
                    Task.status,
                    Task.task_type,
                    Task.stage,
                    Task.error,
                    Task.updated_at,
                    Task.resource_id,
                    Task.account_id,
                    Task.user_id,
                    Task.reject_if_active,
                )
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .with_for_update()
            )
            row = result.one_or_none()
            if row is None:
                return _not_found_result()
            if row.status in (TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value):
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_CANCELLED,
                    f"Task is already {row.status}",
                )
            if row.task_type not in cancellable_types:
                return StoreOperationResult.rejected(
                    StoreOperationCode.NOT_CANCELLABLE,
                    f"Task type '{row.task_type}' does not support cancellation",
                )
            if row.status in {item.value for item in TERMINAL_TASK_STATUSES} and not allow_terminal:
                return StoreOperationResult.rejected(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {row.status}",
                )

            previous = TaskRecord(
                task_id=task_id,
                task_type=row.task_type,
                status=TaskStatus(row.status),
                resource_id=row.resource_id,
                account_id=row.account_id,
                user_id=row.user_id,
                stage=row.stage,
                error=row.error,
                execution_events=await self._event_history(session, task_id),
            )
            task = deepcopy(previous)
            task.status = TaskStatus.CANCELLING
            append_task_events(task, previous)
            values: dict[str, Any] = {
                "status": task.status.value,
            }
            if row.reject_if_active:
                values["active_business_key"] = _business_key(
                    row.account_id, row.user_id, row.task_type, row.resource_id
                )
            await session.execute(
                update(Task)
                .where(*self._task_query(Task, task_id, account_id, user_id))
                .values(
                    **values,
                    updated_at=TaskRecord.next_updated_at(row.updated_at),
                )
            )
            await self._store_event_history(session, task_id, task.execution_events)
            finalized = await self._finalize_if_closed(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if finalized is not None:
                return StoreOperationResult.applied()
            return StoreOperationResult.applied()

    async def add_work(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._require_owner(account_id, user_id)
        Work = self._models.Work
        Task = self._models.Task
        async with self._transaction() as session:
            result = await session.execute(
                select(Task.status)
                .where(*self._task_query(Task, work.task_id, account_id, user_id))
                .with_for_update()
            )
            row = result.one_or_none()
            if row is None:
                return _not_found_result()
            if (
                row.status in {item.value for item in TERMINAL_TASK_STATUSES}
                or row.status == TaskStatus.CANCELLING.value
            ):
                return StoreOperationResult.rejected(
                    StoreOperationCode.ALREADY_TERMINAL
                    if row.status in {item.value for item in TERMINAL_TASK_STATUSES}
                    else StoreOperationCode.INVALID_STATE,
                    f"Task {work.task_id} is {row.status}; rejected work",
                )
            existing = await self._lock_work(session, work.task_id, work.work_id)
            if existing is not None:
                return StoreOperationResult.noop(StoreOperationCode.DUPLICATE)
            session.add(
                Work(
                    service_name=self._service_name,
                    task_id=work.task_id,
                    work_id=work.work_id,
                    account_id=account_id,
                    user_id=user_id,
                    queue_name=work.queue_name,
                    state=work.state.value,
                    requeue_count=work.requeue_count,
                    error=work.error,
                    errors=_json_copy(work.errors),
                    context_count=work.context_count,
                    enqueued_at=work.enqueued_at,
                    started_at=work.started_at,
                    finished_at=work.finished_at,
                    updated_at=work.updated_at,
                )
            )
            return StoreOperationResult.applied()

    async def reconcile_delivery(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        Task = self._models.Task
        async with self._transaction() as session:
            result = await session.execute(
                select(Task.status)
                .where(*self._task_query(Task, work.task_id, account_id, user_id))
                .with_for_update()
            )
            row = result.one_or_none()
            if row is None:
                return _not_found_result()
            if row.status in {item.value for item in TERMINAL_TASK_STATUSES}:
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {row.status}",
                )
            existing = await self._lock_work(session, work.task_id, work.work_id)
            if existing is None:
                session.add(
                    self._models.Work(
                        service_name=self._service_name,
                        task_id=work.task_id,
                        work_id=work.work_id,
                        account_id=account_id,
                        user_id=user_id,
                        queue_name=work.queue_name,
                        state=work.state.value,
                        requeue_count=work.requeue_count,
                        error=work.error,
                        errors=_json_copy(work.errors),
                        context_count=work.context_count,
                        enqueued_at=work.enqueued_at,
                        started_at=work.started_at,
                        finished_at=work.finished_at,
                        updated_at=work.updated_at,
                    )
                )
                return StoreOperationResult.applied()
            elif existing.state in {WorkState.PENDING.value, WorkState.RUNNING.value}:
                if existing.queue_name == work.queue_name:
                    return StoreOperationResult.noop()
                await session.execute(
                    update(self._models.Work)
                    .where(
                        *self._work_query(
                            self._models.Work,
                            work.task_id,
                            work.work_id,
                            None,
                            None,
                        )
                    )
                    .values(queue_name=work.queue_name)
                )
                return StoreOperationResult.applied()
            return StoreOperationResult.noop()

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        Work = self._models.Work
        async with self._transaction() as session:
            if not await self._lock_task(session, task_id, account_id=account_id, user_id=user_id):
                return StoreOperationResult.noop(StoreOperationCode.NOT_FOUND)
            result = await session.execute(
                select(Work)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .with_for_update()
            )
            work = result.scalar_one_or_none()
            if work is None:
                return StoreOperationResult.noop(StoreOperationCode.NOT_FOUND)
            await session.delete(work)
            await session.flush()
            finalized = await self._finalize_if_closed(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if finalized is not None:
                return StoreOperationResult.applied()
            return StoreOperationResult.applied()

    async def get_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> Optional[WorkRecord]:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(self._models.Work).where(
                    *self._work_query(
                        self._models.Work,
                        task_id,
                        work_id,
                        account_id,
                        user_id,
                    )
                )
            )
            return self._work_from_row(row) if row is not None else None

    async def get_works(
        self,
        task_id: str,
        work_ids: Collection[str],
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Dict[str, WorkRecord]:
        async with self._session_factory() as session:
            selected = set(work_ids)
            if not selected:
                return {}
            Work = self._models.Work
            rows = (
                await session.scalars(
                    select(Work).where(
                        *self._work_query(Work, task_id, None, account_id, user_id),
                        Work.work_id.in_(selected),
                    )
                )
            ).all()
            return {row.work_id: self._work_from_row(row) for row in rows}

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        Work = self._models.Work
        async with self._transaction() as session:
            result = await session.execute(
                select(Work.state, Work.started_at)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .with_for_update()
            )
            work = result.one_or_none()
            if work is None or work.state in {WorkState.DONE.value, WorkState.FAILED.value}:
                return StoreOperationResult.rejected(
                    StoreOperationCode.WORK_NOT_OPEN,
                    f"Work {work_id} is unavailable",
                )
            now = time.time()
            await session.execute(
                update(Work)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .values(
                    state=WorkState.RUNNING.value,
                    started_at=work.started_at or now,
                    finished_at=None,
                    updated_at=now,
                )
            )
            return StoreOperationResult.applied()

    async def finish_work(
        self,
        task_id: str,
        work_id: str,
        state: WorkState,
        *,
        error: Optional[str],
        errors: Optional[Collection[str]],
        context_count_delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        if state not in (WorkState.DONE, WorkState.FAILED):
            raise ValueError(f"Non-terminal Work state: {state}")
        Work = self._models.Work
        async with self._transaction() as session:
            if not await self._lock_task(session, task_id, account_id=account_id, user_id=user_id):
                return StoreOperationResult.noop(
                    StoreOperationCode.WORK_NOT_OPEN,
                    f"Work {work_id} is unavailable",
                )
            result = await session.execute(
                select(Work.state, Work.error, Work.context_count)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .with_for_update()
            )
            work = result.one_or_none()
            if work is None or work.state in {
                WorkState.DONE.value,
                WorkState.FAILED.value,
            }:
                return StoreOperationResult.noop(
                    StoreOperationCode.WORK_NOT_OPEN,
                    f"Work {work_id} is unavailable",
                )
            now = time.time()
            values: dict[str, Any] = {
                "state": state.value,
                "finished_at": now,
                "updated_at": now,
            }
            if error:
                values["error"] = sanitize_error(error)
            if state == WorkState.FAILED:
                details = [sanitize_error(str(value)) for value in errors or () if str(value)]
                values["errors"] = details or (
                    [values.get("error", work.error)] if values.get("error", work.error) else []
                )
            if context_count_delta > 0:
                values["context_count"] = work.context_count + context_count_delta
            await session.execute(
                update(Work)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .values(**values)
            )
            finalized = await self._finalize_if_closed(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if finalized is not None:
                return StoreOperationResult.applied()
            return StoreOperationResult.applied()

    async def requeue_work(
        self,
        task_id: str,
        work_id: str,
        *,
        delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        Work = self._models.Work
        async with self._transaction() as session:
            result = await session.execute(
                select(Work.state, Work.requeue_count)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .with_for_update()
            )
            work = result.one_or_none()
            if work is None or work.state in {WorkState.DONE.value, WorkState.FAILED.value}:
                return StoreOperationResult.noop(
                    StoreOperationCode.WORK_NOT_OPEN,
                    f"Work {work_id} is unavailable",
                )
            await session.execute(
                update(Work)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .values(
                    state=WorkState.PENDING.value,
                    requeue_count=work.requeue_count + max(delta, 0),
                    started_at=None,
                    finished_at=None,
                    updated_at=time.time(),
                )
            )
            return StoreOperationResult.applied()

    async def record_work_error(
        self,
        task_id: str,
        work_id: str,
        error: str,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        Work = self._models.Work
        async with self._transaction() as session:
            result = await session.execute(
                select(Work.error)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .with_for_update()
            )
            work = result.one_or_none()
            if work is None:
                return StoreOperationResult.noop(StoreOperationCode.WORK_NOT_OPEN)
            if work.error:
                return StoreOperationResult.noop()
            await session.execute(
                update(Work)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .values(error=sanitize_error(error), updated_at=time.time())
            )
            return StoreOperationResult.applied()

    async def settle_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        Work = self._models.Work
        async with self._transaction() as session:
            if not await self._lock_task(session, task_id, account_id=account_id, user_id=user_id):
                return StoreOperationResult.noop(StoreOperationCode.NOT_FOUND)
            result = await session.execute(
                select(Work.state, Work.error)
                .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                .with_for_update()
            )
            work = result.one_or_none()
            if work is None:
                return StoreOperationResult.noop(StoreOperationCode.NOT_FOUND)
            changed = False
            if work.state not in {WorkState.DONE.value, WorkState.FAILED.value}:
                settled_state = WorkState.FAILED.value if work.error else WorkState.DONE.value
                settled_at = time.time()
                changed = True
                await session.execute(
                    update(Work)
                    .where(*self._work_query(Work, task_id, work_id, account_id, user_id))
                    .values(
                        state=settled_state,
                        finished_at=settled_at,
                        updated_at=settled_at,
                    )
                )
            finalized = await self._finalize_if_closed(
                session,
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            if finalized is not None:
                return StoreOperationResult.applied()
            return StoreOperationResult.applied() if changed else StoreOperationResult.noop()

    async def get_task_status(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Optional[TaskStatus]:
        Task = self._models.Task
        async with self._session_factory() as session:
            status = await session.scalar(
                select(Task.status).where(*self._task_query(Task, task_id, account_id, user_id))
            )
            return TaskStatus(status) if status is not None else None

    async def has_open_work(
        self,
        task_id: str,
        *,
        exclude_work_id: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        Work = self._models.Work
        async with self._session_factory() as session:
            predicates = list(self._work_query(Work, task_id, None, account_id, user_id))
            predicates.append(Work.state.not_in([WorkState.DONE.value, WorkState.FAILED.value]))
            if exclude_work_id is not None:
                predicates.append(Work.work_id != exclude_work_id)
            return bool(await session.scalar(select(exists().where(*predicates))))

    async def summarize_works(
        self,
        task_id: str,
        *,
        work_ids: Optional[Collection[str]],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> WorkSummary:
        async with self._session_factory() as session:
            return summarize_works(
                await self._works(
                    session,
                    task_id,
                    work_ids,
                    account_id=account_id,
                    user_id=user_id,
                )
            )

    async def filter_cancellation_requested(
        self,
        task_ids: Collection[str],
        *,
        account_id: str,
        user_id: str,
    ) -> set[str]:
        ids = set(task_ids)
        if not ids:
            return set()
        Task = self._models.Task
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Task.task_id).where(
                        Task.service_name == self._service_name,
                        Task.task_id.in_(ids),
                        Task.account_id == account_id,
                        Task.user_id == user_id,
                        Task.status.in_([TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value]),
                    )
                )
            ).all()
            return set(rows)

    async def is_cancellation_requested(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        async with self._session_factory() as session:
            Task = self._models.Task
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            *self._task_query(Task, task_id, account_id, user_id),
                            Task.status.in_(
                                [TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value]
                            ),
                        )
                    )
                )
            )
