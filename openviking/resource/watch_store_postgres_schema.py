# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL Watch index keys shared by metadata and startup validation.

Expression strings use PostgreSQL's canonical pg_get_indexdef representation.
The deployment DDL is checked against these keys in the schema consistency test.
"""

from types import MappingProxyType


def postgres_watch_type_name(sql_type: str) -> str:
    """Normalize PostgreSQL aliases; collation is checked separately."""
    name = sql_type.upper().split(" COLLATE ", 1)[0].replace("CHARACTER VARYING", "VARCHAR")
    # PostgreSQL FLOAT without a precision argument is double precision.
    return "DOUBLE PRECISION" if name == "FLOAT" else name


POSTGRES_WATCH_TARGET_PREFIX_CHARS = 128

POSTGRES_WATCH_INDEX_KEYS = MappingProxyType(
    {
        "due": ("service_name", "enabled", "next_run_at"),
        "run": ("service_name", "run_started_at"),
        "target": ("service_name", "account_id", "target_uri_hash"),
        # Bound UTF-8 B-tree entry size while retaining full URI predicates.
        "target_prefix": (
            "service_name",
            "account_id",
            "deleted",
            f"substr(target_uri_key::text, 1, {POSTGRES_WATCH_TARGET_PREFIX_CHARS})",
        ),
        "list_all": ("service_name", "deleted", "created_at", "task_id"),
        # Hash equality keys bound width; queries still check full identities.
        "list": (
            "service_name",
            "md5(account_id::text)",
            "md5(user_id::text)",
            "deleted",
            "created_at",
            "task_id",
        ),
    }
)
