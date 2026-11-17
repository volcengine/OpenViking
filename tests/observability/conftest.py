# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import os
from uuid import uuid4

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def usage_store_factory(request, tmp_path):
    """Run the same public store contract against local or shared persistence."""
    stores = []
    schema = "ov_usage_test_" + uuid4().hex
    dsn = os.environ.get("OV_TEST_USAGE_POSTGRES_DSN")
    if request.param == "postgres":
        if not dsn:
            pytest.skip("set OV_TEST_USAGE_POSTGRES_DSN for real PostgreSQL tests")
        import psycopg
        from psycopg import sql

        from openviking.observability.usage_audit.postgres_store import PostgresUsageAuditStore

    def create(**kwargs):
        if request.param == "postgres":
            store = PostgresUsageAuditStore(dsn, schema=schema, **kwargs)
        else:
            from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore

            store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3", **kwargs)
        stores.append(store)
        return store

    try:
        yield create
    finally:
        for store in stores:
            await store.close()
        if request.param == "postgres":
            with psycopg.connect(dsn, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
