# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Application-scoped dependency binding for complete account stores."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.file import FileStore
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from openviking.server.api_keys.key_mgr import APIKeyManager


def build_api_key_manager(
    root_key: str,
    viking_fs: VikingFS | None,
    *,
    api_key_hashing_enabled: bool = False,
    service_name: str | None = None,
    account_store_provider: str = "file",
    account_store_params: dict[str, object] | None = None,
    account_store_watch_enabled: bool = False,
    account_store_watch_interval_seconds: float = 30.0,
    issue_user_keys: bool = True,
    account_store: AccountStore | None = None,
) -> APIKeyManager:
    """Assemble the API key facade over one complete account store."""
    from openviking.server.api_keys.key_mgr import APIKeyManager

    provider = account_store_provider.strip().lower()
    store = build_account_store(
        viking_fs=viking_fs,
        api_key_hashing_enabled=api_key_hashing_enabled,
        service_name=service_name,
        account_store_provider=provider,
        account_store_params=account_store_params,
        account_store_watch_enabled=account_store_watch_enabled,
        account_store_watch_interval_seconds=account_store_watch_interval_seconds,
        account_store=account_store,
    )
    return APIKeyManager(root_key, store, issue_user_keys=issue_user_keys)


def build_account_store(
    *,
    viking_fs: VikingFS | None,
    api_key_hashing_enabled: bool,
    service_name: str | None = None,
    account_store_provider: str,
    account_store_params: dict[str, object] | None,
    account_store_watch_enabled: bool,
    account_store_watch_interval_seconds: float,
    account_store: AccountStore | None = None,
) -> AccountStore:
    """Create one complete account store from the configured provider."""
    if account_store is not None:
        return account_store

    provider = account_store_provider.strip().lower()
    if provider in {"mysql", "redis_mysql"} and service_name is None:
        raise InvalidArgumentError(
            f"service_name is required for the {provider} account store provider"
        )
    if provider == "file":
        file_system = cast(VikingFS, viking_fs)
        return FileStore(
            viking_fs=file_system,
            params=dict(account_store_params or {}),
            api_key_hashing_enabled=api_key_hashing_enabled,
            watch_enabled=account_store_watch_enabled,
            watch_interval_seconds=account_store_watch_interval_seconds,
        )
    if provider == "mysql":
        try:
            from openviking.server.account_stores.mysql import MySQLAccountStore
        except ImportError:
            raise RuntimeError(
                "MySQL account storage requires openviking[mysql]"
            ) from None
        return MySQLAccountStore(
            service_name=service_name,
            params=account_store_params,
        )
    if provider == "redis_mysql":
        try:
            from openviking.server.account_stores.redis_mysql import (
                RedisMySQLAccountStore,
            )
        except ImportError:
            raise RuntimeError(
                "Redis MySQL account storage requires openviking[redis-mysql]"
            ) from None
        return RedisMySQLAccountStore(
            service_name=service_name,
            params=account_store_params,
        )
    raise InvalidArgumentError(
        f"Unknown account store provider '{account_store_provider}'. "
        "Available providers: file, mysql, redis_mysql"
    )
