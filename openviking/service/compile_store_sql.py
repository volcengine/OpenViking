# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared relational persistence for Compile configurations."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import and_, delete, insert, or_, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from openviking.core.identifiers import (
    validate_account_id,
    validate_identifier_length,
    validate_user_id,
)
from openviking.service.compile_store_sql_schema import make_compile_table
from openviking.service.compile_targets import (
    STATE_FIELDS,
    same_json_value,
    validate_compile_name,
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
_SCHEDULE = ("frequency", "hour", "minute", "weekday")
_CONFIG_COLUMNS = {key: (key,) for key in sorted(STATE_FIELDS)}
_CONFIG_COLUMNS.update(
    {
        "from": ("source_uris",),
        "to": ("target_uri",),
        "skill": ("skill_uri",),
        "update_schedule": tuple(f"schedule_{key}" for key in _SCHEDULE),
    }
)


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


def _after(columns, values):
    """Expand lexicographic keyset comparison for MySQL 5.7 range optimization."""
    return or_(
        *(
            and_(
                *(column == value for column, value in zip(columns[:i], values[:i], strict=True)),
                column > values[i],
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
    """Persist Compile state with atomic writes and scoped account cleanup."""

    dialect: str
    dependency: str
    init_sql: str | None = None

    def __init__(self, params: dict[str, object], service_name: str) -> None:
        options = dict(params)
        self._configs = make_compile_table(options.pop("table", "ov_compile"), self.dialect)
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
                await session.execute(select(*self._configs.c).limit(0))
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
            current["config_id"] = row["config_id"]
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

    async def delete_config(self, account_id: str) -> None:
        """Delete all configs for the given account in this service."""
        if not account_id:
            raise ValueError("Compile account cleanup requires a non-empty account_id")
        configs = self._configs
        scope = (
            configs.c.service_name == self._service_name,
            configs.c.account_id == account_id,
        )
        async with self._write() as session:
            await session.execute(delete(configs).where(*scope))

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
            c.config_id,
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
                record["config_id"] = row["config_id"]
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
