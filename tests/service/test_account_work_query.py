# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Execute account-wait SQL against an isolated relational database."""

from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_store_sql import SQLTaskWorkStore
from openviking.service.task_store_sql_schema import make_task_store_models
from openviking.service.task_tracker import TaskTracker
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
async def test_account_work_query_respects_users_service_and_inline():
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
        tracker = SimpleNamespace(
            _store=SimpleNamespace(
                has_open_work=partial(SQLTaskWorkStore.has_open_work.__wrapped__, store),
                get_queue_work_counts=partial(
                    SQLTaskWorkStore.get_queue_work_counts.__wrapped__, store
                ),
            )
        )
        has_work = partial(TaskTracker.has_work, tracker, exclude_inline=True)
        queue_counts = partial(TaskTracker.get_queue_work_counts, tracker)
        assert await queue_counts(account_id="A") == {
            "Semantic": {"pending": 1, "running": 0},
            "Embedding": {"pending": 0, "running": 1},
        }
        assert await queue_counts(account_id="B") == {"Semantic": {"pending": 1, "running": 0}}
        assert await queue_counts(account_id="absent") == {}
        assert await has_work(account_id="A")
        assert await has_work(account_id="B")
        assert not await has_work(account_id="absent")
        with pytest.raises(ValueError, match="account_id"):
            await has_work()
        with pytest.raises(ValueError, match="account_id"):
            await queue_counts(account_id="")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f'UPDATE "{table}" SET state = :state '
                    "WHERE service_name = :service AND account_id = :account "
                    "AND user_id = :user AND queue_name = :q"
                ),
                {
                    "state": "done",
                    "service": "service",
                    "account": "A",
                    "user": "u1",
                    "q": "Semantic",
                },
            )
        assert await has_work(account_id="A")
        assert not await has_work(account_id="A", user_id="u1")
        assert await queue_counts(account_id="A") == {"Embedding": {"pending": 0, "running": 1}}
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f'UPDATE "{table}" SET state = :state '
                    "WHERE service_name = :service AND account_id = :account "
                    "AND queue_name != 'Inline'"
                ),
                {"state": "done", "service": "service", "account": "A"},
            )
        assert not await has_work(account_id="A")
        assert await has_work(account_id="B")
        assert await queue_counts(account_id="A") == {}
        assert await queue_counts(account_id="B") == {"Semantic": {"pending": 1, "running": 0}}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_account_work_query_aggregates_existing_work_sets():
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    try:
        for task_id, account, user, queue in [
            ("a-u1", "A", "u1", "Semantic"),
            ("a-u2", "A", "u2", "Embedding"),
            ("a-inline", "A", "u1", "Inline"),
            ("b-u1", "B", "u1", "Semantic"),
        ]:
            owner = {"account_id": account, "user_id": user}
            await tracker.create("add_resource", task_id=task_id, **owner)
            await tracker.register_work(task_id, task_id, queue, **owner)
        assert await tracker.get_queue_work_counts(account_id="A") == {
            "Semantic": {"pending": 1, "running": 0},
            "Embedding": {"pending": 1, "running": 0},
        }
        assert await tracker.get_queue_work_counts(account_id="absent") == {}
        assert await tracker.has_work(account_id="A", exclude_inline=True)
        assert not await tracker.has_work(account_id="absent", exclude_inline=True)
        with pytest.raises(ValueError, match="account_id"):
            await tracker.has_work()
        with pytest.raises(ValueError, match="account_id"):
            await tracker.get_queue_work_counts(account_id="")
        await tracker.mark_work_done("a-u1", "a-u1", account_id="A", user_id="u1")
        assert await tracker.has_work(account_id="A", exclude_inline=True)
        assert not await tracker.has_work(account_id="A", user_id="u1", exclude_inline=True)
        assert await tracker.get_queue_work_counts(account_id="A") == {
            "Embedding": {"pending": 1, "running": 0}
        }
        await tracker.mark_work_done("a-u2", "a-u2", account_id="A", user_id="u2")
        assert not await tracker.has_work(account_id="A", exclude_inline=True)
        assert await tracker.has_work(account_id="A")
        assert await tracker.has_work("a-inline", account_id="A", user_id="u1")
        assert await tracker.has_work(account_id="B", exclude_inline=True)
        assert await tracker.get_queue_work_counts(account_id="A") == {}
        assert await tracker.get_queue_work_counts(account_id="B") == {
            "Semantic": {"pending": 1, "running": 0}
        }
    finally:
        await tracker.close()
