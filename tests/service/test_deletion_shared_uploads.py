# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""User deletion must also remove the bucketed shared-upload layout.

New shared uploads are stored at ``upload/<YYYYMMDDHH>/<leaf>/`` while
legacy uploads use ``upload/<upload_id>/``.  ``viking_fs.ls`` is not
recursive, so only expanding the bucket level finds the new layout.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.deletion import DeletionService
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

ROOT = "viking://upload"
BUCKET = "2026110612"
ALICE_LEAF = "a" * 32
BOB_LEAF = "b" * 32
LEGACY_ID = "1700000000000-" + "c" * 32


class _FakeFS:
    def __init__(self, metas):
        self.metas = metas
        self.reads: list[str] = []
        self.removed: list[str] = []

    async def ls(self, uri, **kwargs):
        if uri == ROOT:
            return [
                {"uri": f"{ROOT}/{BUCKET}", "isDir": True, "name": BUCKET},
                {"uri": f"{ROOT}/{LEGACY_ID}", "isDir": True, "name": LEGACY_ID},
            ]
        if uri == f"{ROOT}/{BUCKET}":
            return [
                {"uri": f"{ROOT}/{BUCKET}/{ALICE_LEAF}", "isDir": True, "name": ALICE_LEAF},
                {"uri": f"{ROOT}/{BUCKET}/{BOB_LEAF}", "isDir": True, "name": BOB_LEAF},
            ]
        raise NotFoundError(uri)

    async def read_file(self, uri, ctx=None):
        self.reads.append(uri)
        if uri in self.metas:
            return json.dumps(self.metas[uri])
        raise NotFoundError(uri)

    async def rm(self, uri, recursive=False, ctx=None):
        self.removed.append(uri)


def _service(fs):
    return DeletionService(
        service=SimpleNamespace(viking_fs=fs),
        manager=None,
        service_loop=asyncio.get_running_loop(),
    )


def _ctx():
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)


@pytest.mark.asyncio
async def test_user_deletion_removes_bucketed_shared_upload():
    alice_uri = f"{ROOT}/{BUCKET}/{ALICE_LEAF}"
    bob_uri = f"{ROOT}/{BUCKET}/{BOB_LEAF}"
    legacy_uri = f"{ROOT}/{LEGACY_ID}"
    fs = _FakeFS(
        {
            f"{alice_uri}/meta": {"account": "acct", "user": "alice"},
            f"{bob_uri}/meta": {"account": "acct", "user": "bob"},
            f"{legacy_uri}/meta": {"account": "acct", "user": "alice"},
        }
    )

    await _service(fs)._delete_uploads(_ctx())

    assert f"{alice_uri}/meta" in fs.reads
    assert alice_uri in fs.removed
    assert legacy_uri in fs.removed
    assert bob_uri not in fs.removed
