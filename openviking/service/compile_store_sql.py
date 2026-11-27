# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared relational persistence for Compile configurations and runs."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import and_, delete, insert, or_, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.sql.elements import ColumnElement

from openviking.core.identifiers import (
    validate_account_id,
    validate_identifier_length,
    validate_user_id,
)
from openviking.service.compile_store_sql_schema import make_compile_tables
from openviking.service.compile_targets import (
    RUN_FIELDS,
    STATE_FIELDS,
    TERMINAL,
    same_json_value,
    validate_compile_name,
    validate_task_id,
)
from openviking.storage.sql_runtime import (
    get_shared_sql_runtime,
    sql_runtime_store,
    wait_sql_cleanup,
)
from openviking.storage.sql_session import SQLAsyncSession
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, NotFoundError
from openviking_cli.utils.config.config_utils import normalize_service_name

_PAGE_SIZE = 128
_RUN_STATUSES = TERMINAL | {"queued", "running", "committing"}
_SCHEDULE = ("frequency", "hour", "minute", "weekday")
_RUN_META = (
    "trigger",
    "mode",
    "base_snapshot",
    "start_snapshot",
    "finish_snapshot",
    "skill_snapshot_id",
)
_CONFIG_COLUMNS = {key: (key,) for key in sorted(STATE_FIELDS)}
_CONFIG_COLUMNS.update(
    {
        "from": ("source_uris",),
        "to": ("target_uri",),
        "skill": ("skill_uri",),
        "update_schedule": tuple(f"schedule_{key}" for key in _SCHEDULE),
    }
)
_RUN_COLUMNS = {
    key: (key,)
    for key in (
        "task_id",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "params",
        "result",
        "error",
    )
}
_RUN_COLUMNS.update(actor=("actor_user_id",), meta=_RUN_META)


def _require_fields(values: Mapping[str, Any], fields: Mapping[str, tuple[str, ...]]) -> None:
    if values.keys() - fields.keys():
        raise InvalidArgumentError("Unexpected Compile state fields")


def _project(table, fields, keys):
    return [table.c[name] for key in keys for name in fields.get(key, ())]


def _decode(row, fields, keys) -> dict:
    result = {}
    for key in keys:
        names = fields.get(key, ())
        if not names:
            continue
        if key == "update_schedule":
            result[key] = {
                part: row[name]
                for part, name in zip(_SCHEDULE, names, strict=True)
                if row[name] is not None
            } or None
        elif key == "meta":
            result[key] = {name: row[name] for name in names}
        else:
            result[key] = row[names[0]]
    return result


def _config_values(changes: Mapping[str, Any], owner: str) -> dict:
    _require_fields(changes, _CONFIG_COLUMNS)
    values = {}
    for key, value in changes.items():
        if key == "execution_context" and value.get("owner_user_id") != owner:
            raise InvalidArgumentError("Compile configuration owner cannot change")
        if key == "update_schedule":
            rule = {} if value is None else value
            if value is not None:
                frequency = rule.get("frequency")
                if (
                    rule.keys() - set(_SCHEDULE)
                    or frequency not in ("daily", "weekly")
                    or type(rule.get("hour")) is not int
                    or not 0 <= rule["hour"] < 24
                    or type(rule.get("minute")) is not int
                    or not 0 <= rule["minute"] < 60
                    or (
                        frequency == "weekly"
                        and (type(rule.get("weekday")) is not int or not 1 <= rule["weekday"] <= 7)
                    )
                    or (frequency == "daily" and "weekday" in rule)
                ):
                    raise InvalidArgumentError("Invalid Compile schedule")
            values.update({f"schedule_{part}": rule.get(part) for part in _SCHEDULE})
        else:
            values[_CONFIG_COLUMNS[key][0]] = value
    return values


def _run_values(changes: Mapping[str, Any], dialect: str) -> dict:
    _require_fields(changes, _RUN_COLUMNS)
    if "status" in changes and changes["status"] not in _RUN_STATUSES:
        raise InvalidArgumentError("Invalid Compile run status")
    values = {}
    for key, value in changes.items():
        if key == "meta":
            if value.keys() - set(_RUN_META):
                raise InvalidArgumentError("Unexpected Compile run metadata")
            values.update({name: value.get(name) for name in _RUN_META})
        else:
            if key == "error" and dialect == "postgresql" and isinstance(value, str):
                value = value.replace("\x00", r"\u0000")
            values[_RUN_COLUMNS[key][0]] = value
    return values


def _after(columns, values, descending: bool = False):
    """Expand lexicographic keyset comparison for MySQL 5.7 range optimization."""
    return or_(
        *(
            and_(
                *(column == value for column, value in zip(columns[:i], values[:i], strict=True)),
                column < values[i] if descending else column > values[i],
            )
            for i, column in enumerate(columns)
        )
    )


def _where_key(table, key):
    return tuple(table.c[name] == value for name, value in key.items())


def _validate_id(value: str, name: str, validator) -> None:
    if error := validator(value) or validate_identifier_length(value, name):
        raise InvalidArgumentError(error)


@sql_runtime_store
class SQLCompileStore:
    """Persist Compile state with atomic writes and scoped identity cleanup."""

    dialect: str
    dependency: str
    init_sql: str | None = None

    def __init__(self, params: dict[str, object], service_name: str) -> None:
        options = dict(params)
        self._configs, self._runs = make_compile_tables(
            options.pop("table", "ov_compile"), self.dialect
        )
        self._service_name = normalize_service_name(service_name)
        if len(self._service_name) > 255:
            raise ValueError("Compile SQL service_name must not exceed 255 characters")
        self._url, self._engine_options = self._connection_options(options)
        self._sql_runtime = get_shared_sql_runtime()
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[SQLAsyncSession] | None = None

    @staticmethod
    def _connection_options(options):
        raise NotImplementedError

    @staticmethod
    def _is_duplicate(exc: IntegrityError) -> bool:
        raise NotImplementedError

    @staticmethod
    def _is_schema_error(exc: DBAPIError) -> bool:
        return False

    async def _prepare_schema(self, engine: AsyncEngine) -> None:
        """Server databases are provisioned before startup."""

    async def load(self) -> None:
        if self._engine is not None:
            return
        await self._sql_runtime.acquire()
        engine = None

        async def cleanup() -> None:
            try:
                if engine is not None:
                    await engine.dispose()
            finally:
                await self._sql_runtime.release()

        try:
            engine = create_async_engine(self._url, **self._engine_options)
            await self._prepare_schema(engine)
            sessions = async_sessionmaker(engine, class_=SQLAsyncSession, expire_on_commit=False)
            async with sessions() as session:
                for table in (self._configs, self._runs):
                    await session.execute(select(*table.c).limit(0))
        except ImportError:
            await wait_sql_cleanup(cleanup())
            raise RuntimeError(
                f"Compile {self.dialect} storage requires openviking[{self.dependency}]"
            ) from None
        except DBAPIError as exc:
            await wait_sql_cleanup(cleanup())
            if self._is_schema_error(exc):
                raise RuntimeError(
                    f"Compile {self.dialect} schema is missing or incompatible; provision {self.init_sql}"
                ) from exc
            raise
        except BaseException:
            await wait_sql_cleanup(cleanup())
            raise
        self._engine, self._sessions = engine, sessions

    async def close(self) -> None:
        if self._engine is None:
            return
        engine = self._engine
        self._engine = self._sessions = None

        async def cleanup() -> None:
            try:
                await engine.dispose()
            finally:
                await self._sql_runtime.release()

        await wait_sql_cleanup(cleanup())

    def _session(self) -> SQLAsyncSession:
        if self._sessions is None:
            raise RuntimeError(f"{type(self).__name__}.load() must be called before use")
        return self._sessions()

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[SQLAsyncSession]:
        async with self._session() as session, session.begin():
            if self.dialect == "sqlite":
                # SQLite ignores FOR UPDATE. Take the writer reservation before reading.
                await session.execute(text("BEGIN IMMEDIATE"))
            yield session

    def _key(self, account_id: str, owner: str, name: str) -> dict:
        _validate_id(account_id, "account_id", validate_account_id)
        _validate_id(owner, "user_id", validate_user_id)
        return {
            "service_name": self._service_name,
            "account_id": account_id,
            "owner_user_id": owner,
            "compile_name": validate_compile_name(name),
        }

    def _config_ids(self, key):
        table = self._configs
        return select(table.c.config_id).where(*_where_key(table, key))

    def _run_scope(self, key, task_id: str | None = None) -> ColumnElement[bool]:
        c = self._runs.c
        scope = c.config_id == self._config_ids(key).scalar_subquery()
        if task_id is not None:
            return and_(scope, c.task_id == validate_task_id(task_id))
        return scope

    async def _insert(self, table, values) -> bool:
        try:
            async with self._write() as session:
                await session.execute(insert(table).values(**values))
        except IntegrityError as exc:
            if self._is_duplicate(exc):
                return False
            raise
        return True

    async def create_config(
        self, account_id: str, owner_user_id: str, compile_name: str, record: dict
    ) -> None:
        key = self._key(account_id, owner_user_id, compile_name)
        record = {
            **record,
            "updated_by": owner_user_id,
            "execution_context": {**record["execution_context"], "owner_user_id": owner_user_id},
            "task_first_create_time": time.time(),
        }
        if record.keys() != STATE_FIELDS:
            raise InvalidArgumentError("Compile state fields do not match the configuration schema")
        if record["auto_update"] and record["update_schedule"] is None:
            raise InvalidArgumentError("Scheduled Compile requires an update schedule")
        if not await self._insert(self._configs, {**key, **_config_values(record, owner_user_id)}):
            raise ConflictError("Compile configuration already exists")

    async def update_config(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        changes: dict,
        expected: Mapping[str, Any] | None = None,
    ) -> bool:
        table = self._configs
        key = self._key(account_id, owner_user_id, compile_name)
        where = _where_key(table, key)
        fields = dict(expected or {})
        check_schedule = bool({"auto_update", "update_schedule"} & changes.keys())
        keys = fields.keys() | ({"auto_update", "update_schedule"} if check_schedule else set())
        selected = [table.c.config_id, *_project(table, _CONFIG_COLUMNS, sorted(keys))]
        async with self._write() as session:
            row = (
                (await session.execute(select(*selected).where(*where).with_for_update()))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise NotFoundError(compile_name, "compile configuration")
            current = _decode(row, _CONFIG_COLUMNS, keys)
            if any(not same_json_value(current.get(k), v) for k, v in fields.items()):
                return False
            if check_schedule:
                effective = {**current, **changes}
                if effective["auto_update"] and effective["update_schedule"] is None:
                    raise InvalidArgumentError("Scheduled Compile requires an update schedule")
            values = _config_values(changes, owner_user_id)
            if values:
                await session.execute(
                    update(table).where(table.c.config_id == row["config_id"]).values(**values)
                )
        return True

    async def delete_config(self, account_id: str, owner_user_id: str | None = None) -> None:
        """Delete an account's configs, optionally limited to one owner, in this service."""
        if not account_id:
            raise ValueError("Compile account cleanup requires a non-empty account_id")
        configs, runs = self._configs, self._runs
        scope = (
            configs.c.service_name == self._service_name,
            configs.c.account_id == account_id,
        )
        if owner_user_id is not None:
            scope += (configs.c.owner_user_id == owner_user_id,)
        lock_configs = select(configs.c.config_id).where(*scope).order_by(configs.c.config_id)
        async with self._write() as session:
            # Match create_run's lock order so registration cannot leave an orphan.
            config_ids = (await session.scalars(lock_configs.with_for_update())).all()
            for start in range(0, len(config_ids), _PAGE_SIZE):
                batch = config_ids[start : start + _PAGE_SIZE]
                await session.execute(delete(runs).where(runs.c.config_id.in_(batch)))
                await session.execute(delete(configs).where(configs.c.config_id.in_(batch)))

    async def list_configs(
        self,
        account_id: str | None = None,
        user_id: str | None = None,
        compile_name: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        fresh: bool = False,
    ) -> AsyncIterator[tuple[str, str, dict]]:
        # All sessions use the primary; no store cache to bypass.
        if limit is not None and limit <= 0:
            return
        c = self._configs.c
        filters = [c.service_name == self._service_name]
        order = []
        if account_id is not None:
            _validate_id(account_id, "account_id", validate_account_id)
            filters.append(c.account_id == account_id)
        else:
            order.append(c.account_id)
        if user_id is not None:
            _validate_id(user_id, "user_id", validate_user_id)
            filters.append(c.owner_user_id == user_id)
        if compile_name is not None:
            filters.append(c.compile_name == validate_compile_name(compile_name))
        else:
            order.append(c.compile_name)
        if user_id is None:
            order.append(c.owner_user_id)
        if cursor is not None:
            name, sep, owner = cursor.partition(":")
            validate_compile_name(name)
            if sep:
                _validate_id(owner, "user_id", validate_user_id)
            filters.append(
                _after((c.compile_name, c.owner_user_id), (name, owner))
                if sep
                else c.compile_name > name
            )
        query = select(
            c.account_id,
            c.owner_user_id,
            c.compile_name,
            *_project(self._configs, _CONFIG_COLUMNS, _CONFIG_COLUMNS),
        ).where(*filters)
        after = None
        while limit is None or limit > 0:
            batch_size = _PAGE_SIZE if limit is None else min(limit, _PAGE_SIZE)
            page = query.order_by(*order).limit(batch_size)
            if after is not None:
                page = page.where(_after(order, after))
            rows = await self._sql_runtime.run(lambda statement=page: self._fetch(statement))
            for row in rows:
                record = _decode(row, _CONFIG_COLUMNS, _CONFIG_COLUMNS)
                record["execution_context"]["owner_user_id"] = row["owner_user_id"]
                yield row["account_id"], row["compile_name"], record
            if len(rows) < batch_size or not order:
                return
            after = tuple(rows[-1][column.name] for column in order)
            if limit is not None:
                limit -= len(rows)

    async def _fetch(self, statement):
        async with self._session() as session:
            return (await session.execute(statement)).mappings().all()

    async def _list_run_keys(
        self,
        finished_before: float | None,
        statuses: frozenset[str] | None,
        limit: int,
        after_key: tuple[float, int, str] | None,
    ) -> list[dict]:
        configs, runs = self._configs, self._runs
        c, r = configs.c, runs.c
        order = (r.finished_at, r.config_id, r.task_id)
        service_config = (
            select(c.config_id)
            .where(c.config_id == r.config_id, c.service_name == self._service_name)
            .correlate(runs)
        )
        query = (
            select(*order)
            .where(service_config.exists(), r.finished_at >= 0)
            .order_by(*order)
            .limit(limit)
        )
        if finished_before is not None:
            query = query.where(r.finished_at < finished_before)
        if statuses is not None:
            query = query.where(r.status.in_(statuses))
        if after_key is not None:
            query = query.where(r.finished_at >= after_key[0], _after(order, after_key))
        # Page Run keys before joining their owners so the expiry range drives the scan.
        page = query.subquery()
        keys = (page.c.finished_at, page.c.config_id, page.c.task_id)
        statement = (
            select(c.account_id, c.owner_user_id, c.compile_name, *keys)
            .select_from(page.join(configs, page.c.config_id == c.config_id))
            .order_by(*keys)
        )
        return [dict(row) for row in await self._fetch(statement)]

    async def list_runs(
        self,
        account_id: str | None = None,
        owner_user_id: str | None = None,
        compile_name: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        keys_only: bool = False,
        finished_before: float | None = None,
        statuses: frozenset[str] | None = None,
        after_key: tuple[float, int, str] | None = None,
    ) -> list[dict]:
        if limit is not None and limit <= 0:
            return []
        if keys_only and account_id is owner_user_id is compile_name is None:
            if limit is None or task_id is not None or cursor is not None:
                raise InvalidArgumentError(
                    "Service-wide Run keys require a limit and use after_key for pagination"
                )
            return await self._list_run_keys(
                finished_before=finished_before, statuses=statuses, limit=limit, after_key=after_key
            )
        if account_id is None or owner_user_id is None or compile_name is None:
            raise InvalidArgumentError("A Compile configuration is required for run history")
        if (
            keys_only
            or finished_before is not None
            or statuses is not None
            or after_key is not None
        ):
            raise InvalidArgumentError("Invalid filters for configuration run history")
        table = self._runs
        c = table.c
        key = self._key(account_id, owner_user_id, compile_name)
        where = self._run_scope(key, task_id)
        query = select(*_project(table, _RUN_COLUMNS, _RUN_COLUMNS)).where(where)
        async with self._session() as session:
            if task_id is None:
                if cursor:
                    cursor_error = None
                    try:
                        cursor_id = validate_task_id(cursor)
                    except InvalidArgumentError as exc:
                        cursor_error = exc
                        anchor = None
                    else:
                        anchor = await session.scalar(
                            select(c.created_at).where(where, c.task_id == cursor_id)
                        )
                    if anchor is None:
                        if await session.scalar(select(c.task_id).where(where).limit(1)) is None:
                            return []
                        if cursor_error is not None:
                            raise cursor_error
                        raise InvalidArgumentError("Run cursor does not exist")
                    query = query.where(
                        _after((c.created_at, c.task_id), (anchor, cursor), descending=True)
                    )
                query = query.order_by(c.created_at.desc(), c.task_id.desc()).limit(limit)
            rows = (await session.execute(query)).mappings().all()
        return [_decode(row, _RUN_COLUMNS, _RUN_COLUMNS) for row in rows]

    async def create_run(
        self, account_id: str, owner_user_id: str, compile_name: str, run: dict
    ) -> bool:
        key = self._key(account_id, owner_user_id, compile_name)
        task_id = validate_task_id(run["task_id"])
        _validate_id(run["actor"], "user_id", validate_user_id)
        values = _run_values(run, self.dialect)
        runs = self._runs
        async with self._write() as session:
            # Serialize Run registration for this config.
            config_id = await session.scalar(self._config_ids(key).with_for_update())
            if config_id is None:
                raise NotFoundError(compile_name, "compile configuration")
            existing = await session.scalar(
                select(runs.c.task_id).where(
                    runs.c.config_id == config_id, runs.c.task_id == task_id
                )
            )
            if existing is not None:
                return False
            await session.execute(insert(runs).values(config_id=config_id, **values))
        return True

    async def update_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        changes: dict,
        expected: Mapping[str, Any] | None = None,
        replace: bool = False,
    ) -> bool:
        if replace and RUN_FIELDS - changes.keys():
            raise InvalidArgumentError("Compile run replacement requires a complete record")
        table = self._runs
        key = self._key(account_id, owner_user_id, compile_name)
        async with self._write() as session:
            where = await self._matching_run_key(session, key, task_id, expected or {})
            if where is None:
                return False
            if changes.get("task_id", task_id) != task_id:
                raise InvalidArgumentError("Compile run task ID cannot change")
            values = _run_values(changes, self.dialect)
            values.pop("task_id", None)
            if "actor" in changes:
                _validate_id(changes["actor"], "user_id", validate_user_id)
            if values:
                await session.execute(update(table).where(*where).values(**values))
        return True

    async def delete_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        expected: Mapping[str, Any],
    ) -> bool:
        key = self._key(account_id, owner_user_id, compile_name)
        table = self._runs
        async with self._write() as session:
            where = await self._matching_run_key(session, key, task_id, expected)
            if where is None:
                return False
            return (await session.execute(delete(table).where(*where))).rowcount > 0

    async def _matching_run_key(self, session, key, task_id, expected):
        table = self._runs
        columns = [table.c.config_id, *_project(table, _RUN_COLUMNS, expected)]
        row = (
            (
                await session.execute(
                    select(*columns).where(self._run_scope(key, task_id)).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        current = _decode(row, _RUN_COLUMNS, expected)
        if not all(current.get(k) == v for k, v in expected.items()):
            return None
        return (table.c.config_id == row["config_id"], table.c.task_id == task_id)
