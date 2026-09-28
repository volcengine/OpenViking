# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _AclAGFS:
    """Async AGFS stub that records stat/lock ordering for ACL updates."""

    def __init__(self, is_dir):
        self.is_dir = is_dir
        self.events = []

    async def stat(self, path):
        self.events.append(("stat", path))
        if self.is_dir is None:
            raise FileNotFoundError(path)
        return {"isDir": self.is_dir}

    async def pathlock_acquire_exact(self, path):
        self.events.append(("exact", path))
        return {"lease_ref": "exact"}

    async def pathlock_acquire_tree(self, path):
        self.events.append(("tree", path))
        return {"lease_ref": "tree"}

    async def pathlock_release(self, lease):
        self.events.append(("release", lease["lease_ref"]))


def _fs(agfs):
    fs = VikingFS(agfs=SimpleNamespace())
    fs._async_agfs = agfs  # type: ignore[assignment]
    ctx = RequestContext(user=UserIdentifier("default", "alice"), role=Role.ADMIN)
    fs._ensure_acl_manage = AsyncMock(return_value=ctx)  # type: ignore[method-assign]
    fs._uri_to_path = lambda uri, **_: "/local/default/resources/target"  # type: ignore[method-assign]
    fs.acl_manager = SimpleNamespace(
        set_acl=AsyncMock(return_value="effective"),
        get_direct=AsyncMock(return_value=SimpleNamespace(entries=[])),
        to_report=lambda uri, effective: {"uri": uri, "effective": effective},
    )
    return fs


@pytest.mark.asyncio
async def test_set_acl_missing_target_returns_not_found_without_locking():
    agfs = _AclAGFS(is_dir=None)
    fs = _fs(agfs)
    with pytest.raises(NotFoundError):
        await fs.set_acl("viking://resources/target", entries=[])
    assert [e[0] for e in agfs.events] == ["stat"]


@pytest.mark.asyncio
async def test_grant_acl_missing_target_returns_not_found_without_locking():
    agfs = _AclAGFS(is_dir=None)
    fs = _fs(agfs)
    with pytest.raises(NotFoundError):
        await fs.grant_acl("viking://resources/target", "user:bob", "read")
    assert [e[0] for e in agfs.events] == ["stat"]


@pytest.mark.asyncio
@pytest.mark.parametrize("is_dir,kind", [(False, "exact"), (True, "tree")])
async def test_set_acl_locks_existing_target_by_type(is_dir, kind):
    agfs = _AclAGFS(is_dir=is_dir)
    fs = _fs(agfs)
    result = await fs.set_acl("viking://resources/target", entries=[])
    assert result["effective"] == "effective"
    kinds = [e[0] for e in agfs.events]
    assert kinds[0] == "stat"
    assert kinds[1] == kind
    assert kinds[-1] == "release"
