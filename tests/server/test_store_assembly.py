# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.server.api_keys.legacy import FileStore
from openviking.server.config import AccountStoreConfig
from openviking.server.store_assembly import (
    build_account_store,
    build_api_key_manager,
)
from openviking_cli.exceptions import InvalidArgumentError
from tests.server.account_store_fakes import InMemoryAGFS


def test_unknown_account_store_is_rejected():
    with pytest.raises(InvalidArgumentError, match="Unknown account store provider"):
        build_account_store(
            viking_fs=SimpleNamespace(agfs=InMemoryAGFS()),
            api_key_hashing_enabled=False,
            account_store_provider="missing",
            account_store_params=None,
            account_store_watch_enabled=False,
            account_store_watch_interval_seconds=30,
        )


@pytest.mark.parametrize("injected", [False, True])
async def test_assembled_manager_supports_account_authentication(injected):
    account_store = FileStore(SimpleNamespace(agfs=InMemoryAGFS()))
    manager = build_api_key_manager(
        "root",
        None if injected else SimpleNamespace(agfs=InMemoryAGFS()),
        account_store_provider=AccountStoreConfig(provider=" FILE ").provider,
        account_store=account_store if injected else None,
    )
    await manager.load()
    try:
        key = await manager.create_account("acme", "alice")
        assert (await manager.resolve_identity(key)).user_id == "alice"
    finally:
        await manager.close()
