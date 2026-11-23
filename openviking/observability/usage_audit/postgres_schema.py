# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Mappings for the independently provisioned PostgreSQL usage/audit schema."""

import re

from sqlalchemy import BigInteger, Column, Double, Identity, Integer, MetaData, Table, Text

DEFAULT_SCHEMA = "openviking_usage_audit"
POSTGRES_SCHEMA_VERSION = 2
PROVISION_SQL = "openviking/observability/usage_audit/sql/001_usage_audit_postgres.sql"


def validate_schema(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise ValueError(
            "Usage/audit PostgreSQL schema must be a lowercase SQL identifier (1..63 characters)"
        )
    return value


def validate_lock_timeout(value: object) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        raise ValueError("Usage/audit lock_timeout_ms must be an integer (1..60000)") from None
    if isinstance(value, bool) or str(timeout) != str(value) or not 1 <= timeout <= 60_000:
        raise ValueError("Usage/audit lock_timeout_ms must be an integer (1..60000)")
    return timeout


def make_tables(schema: str) -> dict[str, Table]:
    metadata = MetaData(schema=validate_schema(schema))

    def key(name: str, *, primary: bool = True, nullable: bool = False) -> Column:
        return Column(name, Text(collation="C"), primary_key=primary, nullable=nullable)

    def hourly(name: str, dimensions: tuple[str, ...], counts: tuple[str, ...]) -> Table:
        return Table(
            name,
            metadata,
            key("account_id"),
            key("user_id"),
            key("date_utc"),
            Column("hour_utc", Integer, primary_key=True),
            *(key(dimension) for dimension in dimensions),
            *(Column(count, BigInteger, nullable=False) for count in counts),
            key("updated_at", primary=False),
        )

    hourly(
        "usage_token_hourly", ("source", "token_type", "provider", "model_name"), ("token_count",)
    )
    hourly("usage_retrieval_hourly", ("operation", "status"), ("request_count", "result_count"))
    hourly("usage_context_write_bucket", ("operation",), ("count",))
    Table(
        "request_audit",
        metadata,
        Column("id", BigInteger, Identity(), primary_key=True),
        key("request_id", primary=False, nullable=True),
        key("account_id", primary=False),
        key("user_id", primary=False, nullable=True),
        *(key(name, primary=False) for name in ("method", "route", "api_type")),
        Column("status_code", Integer, nullable=False),
        Column("duration_ms", Double, nullable=False),
        *(
            key(name, primary=False, nullable=True)
            for name in ("error_code", "error_message", "error_details")
        ),
        key("created_at", primary=False),
    )
    Table("_schema_meta", metadata, key("key"), key("value", primary=False))
    Table(
        "usage_deletion_fence",
        metadata,
        key("account_id"),
        key("scope"),
        key("user_id"),
        Column("generation", BigInteger, nullable=False),
    )
    return {table.name: table for table in metadata.tables.values()}
