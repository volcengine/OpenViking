# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Relational configuration table shared by the SQL providers."""

import re

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Double,
    Identity,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql, postgresql

from openviking.core.identifiers import MAX_IDENTIFIER_LENGTH


def make_compile_table(prefix: object = "ov_compile", dialect: str = "mysql") -> Table:
    if dialect not in {"mysql", "postgresql", "sqlite"}:
        raise ValueError(f"Unsupported Compile SQL dialect: {dialect}")
    # Keep the provider-specific limits for generated SQL identifiers.
    maximum = {"postgresql": 47, "mysql": 51, "sqlite": 56}[dialect]
    if (
        not isinstance(prefix, str)
        or len(prefix) > maximum
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", prefix)
    ):
        raise ValueError(
            f"compile_store.table must be a 1–{maximum} character ASCII SQL identifier prefix"
        )
    metadata = MetaData()
    is_mysql = dialect == "mysql"
    collation = "C" if dialect == "postgresql" else "BINARY"
    json_type = postgresql.JSONB if dialect == "postgresql" else mysql.JSON if is_mysql else JSON
    timestamp = mysql.DOUBLE(asdecimal=False) if is_mysql else Double()
    schedule_number = mysql.TINYINT(unsigned=True) if is_mysql else SmallInteger()
    options = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_bin",
        "mysql_row_format": "DYNAMIC",
    }

    def unicode_text(length: int) -> String:
        return (
            mysql.VARCHAR(length, charset="utf8mb4", collation="utf8mb4_bin")
            if is_mysql
            else String(length, collation=collation)
        )

    def ascii_text(length: int) -> String:
        return (
            mysql.VARCHAR(length, charset="ascii", collation="ascii_bin")
            if is_mysql
            else unicode_text(length)
        )

    def identifier_check(column: str) -> tuple[CheckConstraint, ...]:
        if dialect == "postgresql":
            return (CheckConstraint(f"{column} ~ '^[A-Za-z0-9_.@-]+$'"),)
        return ()

    config_id_type = BigInteger().with_variant(Integer(), "sqlite")
    configs = Table(
        f"{prefix}_configs",
        metadata,
        Column(
            "config_id",
            config_id_type,
            *([Identity(always=True)] if dialect == "postgresql" else []),
            primary_key=True,
        ),
        Column("service_name", ascii_text(MAX_IDENTIFIER_LENGTH), nullable=False),
        Column("account_id", ascii_text(MAX_IDENTIFIER_LENGTH), nullable=False),
        Column("owner_user_id", ascii_text(MAX_IDENTIFIER_LENGTH), nullable=False),
        Column("compile_name", unicode_text(64), nullable=False),
        Column("updated_by", unicode_text(MAX_IDENTIFIER_LENGTH), nullable=False),
        Column("source_uris", json_type, nullable=False),
        Column("target_uri", Text, nullable=False),
        Column("skill_uri", Text, nullable=False),
        Column("instruction", mysql.LONGTEXT if is_mysql else Text),
        Column("args", json_type, nullable=False),
        Column("auto_update", Boolean, nullable=False),
        Column(
            "schedule_frequency", mysql.ENUM("daily", "weekly") if is_mysql else unicode_text(6)
        ),
        Column("schedule_hour", schedule_number),
        Column("schedule_minute", schedule_number),
        Column("schedule_weekday", schedule_number),
        Column("task_first_create_time", timestamp, nullable=False),
        Column("last_run_time", timestamp),
        Column("last_triggered_slot", unicode_text(40)),
        Column("next_triggered_slot", unicode_text(40)),
        Column("progress", json_type, nullable=False),
        Column("execution_context", json_type, nullable=False),
        *identifier_check("account_id"),
        *identifier_check("owner_user_id"),
        UniqueConstraint(
            "service_name",
            "account_id",
            "compile_name",
            "owner_user_id",
            name="uq_compile_configs_name" if is_mysql else f"uq_{prefix}_configs_name",
        ),
        **(options if is_mysql else {}),
    )
    return configs
