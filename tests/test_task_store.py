# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for persistent task storage."""

import pytest

from openviking.pyagfs.exceptions import AGFSPluginError
from openviking.service.task_store import PersistentTaskStore

pytestmark = pytest.mark.asyncio


class _MkdirPluginErrorAgfs:
    def __init__(self, message: str) -> None:
        self._message = message

    def mkdir(self, path: str, mode: str = "755"):
        raise AGFSPluginError(self._message)


async def test_mkdir_if_missing_ignores_localfs_file_exists_plugin_error():
    store = PersistentTaskStore(
        _MkdirPluginErrorAgfs("plugin error: failed to create directory: File exists (os error 17)")
    )

    await store._mkdir_if_missing("/local/acme")


async def test_mkdir_if_missing_propagates_unrelated_plugin_error():
    store = PersistentTaskStore(_MkdirPluginErrorAgfs("plugin error: permission denied"))

    with pytest.raises(AGFSPluginError, match="permission denied"):
        await store._mkdir_if_missing("/local/acme")
