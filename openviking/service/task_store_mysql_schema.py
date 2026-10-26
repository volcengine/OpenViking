# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLAlchemy models for the relational Task/Work store."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DOUBLE, JSON, VARBINARY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The longest derived name is ix_<prefix>_owner_internal_created (26 extra chars).
_MAX_TABLE_PREFIX_LENGTH = 64 - len("ix__owner_internal_created")


def validate_table_prefix(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_TABLE_PREFIX_LENGTH
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value)
    ):
        raise ValueError(
            "MySQL task_store.table must start with a letter, contain only letters, "
            f"digits, or underscores, and be at most {_MAX_TABLE_PREFIX_LENGTH} characters"
        )
    return value


@dataclass(frozen=True)
class TaskStoreModels:
    Base: type[DeclarativeBase]
    Task: Any
    Work: Any
    Event: Any
    table_prefix: str

    @property
    def table_names(self) -> tuple[str, str, str]:
        return self.Task.__tablename__, self.Work.__tablename__, self.Event.__tablename__


def make_task_store_models(table_prefix: str = "ov_tasks") -> TaskStoreModels:
    """Build isolated mappings for one configured table namespace."""
    table_prefix = validate_table_prefix(table_prefix)
    work_table = f"{table_prefix}_works"
    event_table = f"{table_prefix}_execution_events"

    class Base(DeclarativeBase):
        pass

    class Task(Base):
        __tablename__ = table_prefix
        __table_args__ = (
            UniqueConstraint(
                "active_business_key",
                name=f"uq_{table_prefix}_active_business_key",
            ),
            Index(
                f"ix_{table_prefix}_owner_created",
                "account_id",
                "user_id",
                "created_at",
                "task_id",
            ),
            Index(
                f"ix_{table_prefix}_account_created",
                "account_id",
                "created_at",
                "task_id",
            ),
            Index(
                f"ix_{table_prefix}_created",
                "created_at",
                "task_id",
            ),
            Index(
                f"ix_{table_prefix}_owner_status",
                "account_id",
                "user_id",
                "status",
            ),
            Index(
                f"ix_{table_prefix}_owner_internal_created",
                "account_id",
                "user_id",
                "is_internal",
                "created_at",
                "task_id",
            ),
            Index(
                f"ix_{table_prefix}_expiry",
                "status",
                "updated_at",
                "task_id",
            ),
            {
                "mysql_engine": "InnoDB",
                "mysql_charset": "utf8mb4",
                "mysql_row_format": "DYNAMIC",
            },
        )

        task_id: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"), primary_key=True)
        task_type: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"))
        status: Mapped[str] = mapped_column(String(16, collation="ascii_bin"))
        created_at: Mapped[float] = mapped_column(DOUBLE)
        updated_at: Mapped[float] = mapped_column(DOUBLE)
        resource_id: Mapped[Optional[str]] = mapped_column(
            Text(collation="utf8mb4_bin"), nullable=True
        )
        account_id: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"))
        user_id: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"))
        meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
        stage: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
        error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        auth: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
        processing_seconds: Mapped[Optional[float]] = mapped_column(DOUBLE, nullable=True)
        reject_if_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        active_business_key: Mapped[Optional[bytes]] = mapped_column(VARBINARY(32), nullable=True)

    class Work(Base):
        __tablename__ = work_table
        __table_args__ = (
            Index(
                f"ix_{work_table}_state",
                "task_id",
                "state",
            ),
            Index(
                f"ix_{work_table}_queue",
                "task_id",
                "queue_name",
                "state",
            ),
            Index(
                f"ix_{work_table}_owner",
                "account_id",
                "user_id",
                "task_id",
            ),
            {
                "mysql_engine": "InnoDB",
                "mysql_charset": "utf8mb4",
                "mysql_row_format": "DYNAMIC",
            },
        )

        task_id: Mapped[str] = mapped_column(
            String(255, collation="utf8mb4_bin"),
            ForeignKey(
                f"{table_prefix}.task_id",
                ondelete="CASCADE",
                name=f"fk_{work_table}_task",
            ),
            primary_key=True,
        )
        work_id: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"), primary_key=True)
        account_id: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"))
        user_id: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"))
        queue_name: Mapped[str] = mapped_column(String(255, collation="utf8mb4_bin"))
        state: Mapped[str] = mapped_column(String(16, collation="ascii_bin"))
        requeue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
        error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
        context_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
        enqueued_at: Mapped[float] = mapped_column(DOUBLE)
        started_at: Mapped[Optional[float]] = mapped_column(DOUBLE, nullable=True)
        finished_at: Mapped[Optional[float]] = mapped_column(DOUBLE, nullable=True)
        updated_at: Mapped[float] = mapped_column(DOUBLE)

    class Event(Base):
        __tablename__ = event_table
        __table_args__ = (
            {
                "mysql_engine": "InnoDB",
                "mysql_charset": "utf8mb4",
                "mysql_row_format": "DYNAMIC",
            },
        )

        task_id: Mapped[str] = mapped_column(
            String(255, collation="utf8mb4_bin"),
            ForeignKey(
                f"{table_prefix}.task_id",
                ondelete="CASCADE",
                name=f"fk_{event_table}_task",
            ),
            primary_key=True,
        )
        seq: Mapped[int] = mapped_column(Integer, primary_key=True)
        recorded_at: Mapped[str] = mapped_column(String(64, collation="ascii_bin"))
        kind: Mapped[str] = mapped_column(String(64, collation="utf8mb4_bin"))
        status: Mapped[str] = mapped_column(String(16, collation="ascii_bin"))
        stage: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
        operation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        dropped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
        started_mid_task: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    return TaskStoreModels(Base, Task, Work, Event, table_prefix)
