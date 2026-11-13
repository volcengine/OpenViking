# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""User deletion must remove shared uploads in both storage layouts."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.deletion import DeletionService
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

ROOT = "viking://upload"
BUCKET = "2026010100"
ALICE_LEAF = f"{ROOT}/{BUCKET}/{'a' * 32}"
BOB_LEAF = f"{ROOT}/{BUCKET}/{'b' * 32}"
LEGACY_ALICE = f"{ROOT}/legacy-alice"


class _FakeVikingFS:
    def __init__(self) -> None:
        self.children = {
            ROOT: [
                {"uri": f"{ROOT}/{BUCKET}", "isDir": True},
                {"uri": LEGACY_ALICE, "isDir": True},
            ],
            f"{ROOT}/{BUCKET}": [
                {"uri": ALICE_LEAF, "isDir": True},
                {"uri": BOB_LEAF, "isDir": True},
            ],
        }
        self.meta = {
            f"{ALICE_LEAF}/meta": {"account": "acct", "user": "alice"},
            f"{BOB_LEAF}/meta": {"account": "acct", "user": "bob"},
            f"{LEGACY_ALICE}/meta": {"account": "acct", "user": "alice"},
        }
        self.fail_uris: set[str] = set()
        self.removed: list[str] = []

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
        del show_all_hidden, node_limit, ctx
        return list(self.children.get(uri.rstrip("/"), []))

    async def read_file(self, uri, ctx=None):
        del ctx
        if uri in self.fail_uris:
            raise OSError(f"storage temporarily unavailable: {uri}")
        if uri not in self.meta:
            raise NotFoundError(uri, "shared upload metadata")
        return json.dumps(self.meta[uri])

    async def rm(self, uri, recursive=False, ctx=None):
        del recursive, ctx
        self.removed.append(uri)


def _deletion_service(fake_fs: _FakeVikingFS) -> DeletionService:
    return DeletionService(
        service=SimpleNamespace(viking_fs=fake_fs),
        manager=None,
        service_loop=asyncio.get_running_loop(),
    )


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


@pytest.mark.asyncio
async def test_delete_uploads_covers_bucketed_and_legacy_layouts():
    fake_fs = _FakeVikingFS()

    await _deletion_service(fake_fs)._delete_uploads(_ctx())

    # Alice's bucketed leaf and legacy upload are removed; Bob's leaf is kept.
    assert fake_fs.removed == [ALICE_LEAF, LEGACY_ALICE]


@pytest.mark.asyncio
async def test_unreadable_upload_metadata_is_not_treated_as_absent():
    fake_fs = _FakeVikingFS()
    fake_fs.fail_uris.add(f"{ALICE_LEAF}/meta")

    with pytest.raises(OSError, match="storage temporarily unavailable"):
        await _deletion_service(fake_fs)._delete_uploads(_ctx())

    assert fake_fs.removed == []
