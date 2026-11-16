# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Execute account-wait SQL against an isolated relational database."""

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_store_sql import SQLTaskWorkStore
from openviking.service.task_store_sql_schema import make_task_store_models


@pytest.mark.asyncio
async def test_open_account_work_counts_all_users_and_service_partition():
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite://")
    models = make_task_store_models("ov_count", "postgresql")
    store = SimpleNamespace(
        _models=models,
        _service_name="service",
        _session_factory=async_sessionmaker(engine),
    )
    table = models.Work.__tablename__
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f'CREATE TABLE "{table}" '
                    "(service_name TEXT, account_id TEXT, user_id TEXT, queue_name TEXT, state TEXT)"
                )
            )
            for service, account, user, queue, state in [
                ("service", "A", "u1", "Semantic", "pending"),
                ("service", "A", "u2", "Embedding", "running"),
                ("service", "A", "u1", "Inline", "running"),
                ("service", "A", "u1", "Semantic", "done"),
                ("service", "A", "u1", "Semantic", "failed"),
                ("service", "A", "u1", "Semantic", "cancelled"),
                ("service", "B", "u1", "Semantic", "pending"),
                ("other", "A", "u1", "Semantic", "pending"),
            ]:
                await conn.execute(
                    text(f'INSERT INTO "{table}" VALUES (:s, :a, :u, :q, :state)'),
                    {"s": service, "a": account, "u": user, "q": queue, "state": state},
                )
        # Use the real implementation; SQLite sessions run on this test's event loop.
        count = SQLTaskWorkStore.count_open_account_work.__wrapped__
        assert await count(store, account_id="A") == 2
        assert await count(store, account_id="B") == 1
        assert await count(store, account_id="absent") == 0
        async with engine.begin() as conn:
            await conn.execute(
                text(f'UPDATE "{table}" SET state = :state WHERE account_id = :account'),
                {"state": "done", "account": "A"},
            )
        assert await count(store, account_id="A") == 0
        assert await count(store, account_id="B") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_store_does_not_silently_use_global_work():
    with pytest.raises(NotImplementedError, match="SQL"):
        await PersistentTaskStore.count_open_account_work(object(), account_id="A")
