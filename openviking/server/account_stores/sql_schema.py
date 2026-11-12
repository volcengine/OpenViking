# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared SQLAlchemy schema for relational account-store providers.

``service_name`` is the tenant boundary. It is deliberately present in every
primary lookup, unique key, and foreign-key relationship so one database can
hold many OpenViking deployments safely.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _identifier(length: int, *, ascii_only: bool = False):
    return String(length, collation="C").with_variant(
        String(length, collation="ascii_bin" if ascii_only else "utf8mb4_bin"), "mysql"
    )


_timestamp = DateTime(timezone=True).with_variant(DATETIME(fsp=6), "mysql")
_material = Text(collation="C").with_variant(Text(collation="ascii_bin"), "mysql")


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "ov_accounts"
    __table_args__ = (
        UniqueConstraint(
            "service_name",
            "account_id",
            name="uq_ov_accounts_account_identity",
        ),
        UniqueConstraint(
            "service_name",
            "account_pk",
            name="uq_ov_accounts_account_service_pk",
        ),
        Index(
            "ix_ov_accounts_account_deletion",
            "service_name",
            "deletion_task_id",
        ),
        Index(
            "ix_ov_accounts_account_created",
            "service_name",
            "created_at",
            "account_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    account_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(_identifier(255))
    account_id: Mapped[str] = mapped_column(_identifier(255))
    created_at: Mapped[object] = mapped_column(
        _timestamp, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    deletion_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    deletion_owner_account_id: Mapped[Optional[str]] = mapped_column(
        _identifier(255), nullable=True
    )
    deletion_owner_user_id: Mapped[Optional[str]] = mapped_column(_identifier(255), nullable=True)


class User(Base):
    __tablename__ = "ov_accounts_users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_name", "account_pk"],
            ["ov_accounts.service_name", "ov_accounts.account_pk"],
            ondelete="CASCADE",
            name="fk_ov_accounts_user_account",
        ),
        UniqueConstraint(
            "service_name",
            "account_pk",
            "user_id",
            name="uq_ov_accounts_user_identity",
        ),
        UniqueConstraint(
            "service_name",
            "user_pk",
            name="uq_ov_accounts_user_service_pk",
        ),
        UniqueConstraint(
            "service_name",
            "account_pk",
            "user_pk",
            name="uq_ov_accounts_user_account_pk",
        ),
        Index(
            "ix_ov_accounts_user_admin",
            "service_name",
            "account_pk",
            "role",
        ),
        Index(
            "ix_ov_accounts_user_deletion",
            "service_name",
            "deletion_task_id",
        ),
        Index(
            "ix_ov_accounts_user_active_created",
            "service_name",
            "account_pk",
            "deletion_task_id",
            "created_at",
            "user_pk",
        ),
        Index(
            "ix_ov_accounts_user_role_active_created",
            "service_name",
            "account_pk",
            "role",
            "deletion_task_id",
            "created_at",
            "user_pk",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    user_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(_identifier(255))
    account_pk: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[str] = mapped_column(_identifier(255))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[object] = mapped_column(
        _timestamp, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    deletion_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    deletion_owner_account_id: Mapped[Optional[str]] = mapped_column(
        _identifier(255), nullable=True
    )
    deletion_owner_user_id: Mapped[Optional[str]] = mapped_column(_identifier(255), nullable=True)


class Group(Base):
    __tablename__ = "ov_accounts_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_name", "account_pk"],
            ["ov_accounts.service_name", "ov_accounts.account_pk"],
            ondelete="CASCADE",
            name="fk_ov_accounts_group_account",
        ),
        UniqueConstraint(
            "service_name",
            "account_pk",
            "group_id",
            name="uq_ov_accounts_group_identity",
        ),
        UniqueConstraint(
            "service_name",
            "group_pk",
            name="uq_ov_accounts_group_service_pk",
        ),
        UniqueConstraint(
            "service_name",
            "account_pk",
            "group_pk",
            name="uq_ov_accounts_group_account_pk",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    group_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(_identifier(255))
    account_pk: Mapped[int] = mapped_column(BigInteger)
    group_id: Mapped[str] = mapped_column(_identifier(255))
    created_at: Mapped[object] = mapped_column(
        _timestamp, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class GroupMember(Base):
    __tablename__ = "ov_accounts_group_members"
    __table_args__ = (
        PrimaryKeyConstraint("service_name", "account_pk", "group_pk", "user_pk"),
        ForeignKeyConstraint(
            ["service_name", "account_pk", "group_pk"],
            [
                "ov_accounts_groups.service_name",
                "ov_accounts_groups.account_pk",
                "ov_accounts_groups.group_pk",
            ],
            ondelete="CASCADE",
            name="fk_ov_accounts_member_group",
        ),
        ForeignKeyConstraint(
            ["service_name", "account_pk", "user_pk"],
            [
                "ov_accounts_users.service_name",
                "ov_accounts_users.account_pk",
                "ov_accounts_users.user_pk",
            ],
            ondelete="CASCADE",
            name="fk_ov_accounts_member_user",
        ),
        Index(
            "ix_ov_accounts_member_user",
            "service_name",
            "account_pk",
            "user_pk",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    service_name: Mapped[str] = mapped_column(_identifier(255))
    account_pk: Mapped[int] = mapped_column(BigInteger)
    group_pk: Mapped[int] = mapped_column(BigInteger)
    user_pk: Mapped[int] = mapped_column(BigInteger)


class Credential(Base):
    __tablename__ = "ov_accounts_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_name", "user_pk"],
            ["ov_accounts_users.service_name", "ov_accounts_users.user_pk"],
            ondelete="CASCADE",
            name="fk_ov_accounts_credential_user",
        ),
        UniqueConstraint(
            "service_name",
            "active_user_pk",
            name="uq_ov_accounts_active_credential",
        ),
        Index(
            "ix_ov_accounts_credential_lookup",
            "service_name",
            "lookup_digest",
            "revoked",
            "user_pk",
        ),
        Index(
            "ix_ov_accounts_credential_history",
            "service_name",
            "user_pk",
            "revoked",
            "sequence",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    credential_id: Mapped[str] = mapped_column(_identifier(32, ascii_only=True), primary_key=True)
    service_name: Mapped[str] = mapped_column(_identifier(255))
    user_pk: Mapped[int] = mapped_column(BigInteger)
    material: Mapped[str] = mapped_column(_material)
    lookup_digest: Mapped[str] = mapped_column(_identifier(64, ascii_only=True))
    prefix: Mapped[str] = mapped_column(_identifier(8, ascii_only=True))
    revoked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[object] = mapped_column(
        _timestamp, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    revoked_at: Mapped[Optional[object]] = mapped_column(_timestamp, nullable=True)
    active_user_pk: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
