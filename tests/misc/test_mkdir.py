# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import get_binding_client
from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSResourceExhaustedError
from openviking.storage.viking_fs import VikingFS


@pytest.fixture
def vfs(tmp_path):
    client_type, _ = get_binding_client()
    client = client_type()
    try:
        client.mount("localfs", "/local", {"local_dir": str(tmp_path)})
        yield VikingFS(agfs=client)
    finally:
        client.close()


async def test_mkdir_ensures_parents_first(vfs):
    parent = "viking://resources/parent"
    target = f"{parent}/child"

    await vfs.mkdir(target, exist_ok=True)
    assert (await vfs.stat(parent))["isDir"]
    assert (await vfs.stat(target))["isDir"]

    await vfs.mkdir(target, exist_ok=True)
    with pytest.raises(AGFSAlreadyExistsError):
        await vfs.mkdir(target)


async def test_mkdir_exist_ok_true_propagates_non_exists_error(vfs, monkeypatch):
    target = "viking://resources/full_dir"
    monkeypatch.setattr(
        vfs._async_agfs,
        "mkdir",
        AsyncMock(side_effect=AGFSResourceExhaustedError("quota exceeded")),
    )

    with pytest.raises(AGFSResourceExhaustedError, match="quota exceeded"):
        await vfs.mkdir(target, exist_ok=True)

    assert not await vfs.exists(target)
