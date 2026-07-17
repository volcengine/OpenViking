# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integrity regressions for temporary uploads resolved at consume time."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.temp_upload_store import TempUploadStore
from openviking_cli.session.user_id import UserIdentifier


def test_resolve_local_fingerprints_consumed_file_bytes(upload_temp_dir: Path):
    temp_file_id = "upload_integrity.bin"
    upload_path = upload_temp_dir / temp_file_id
    upload_path.write_bytes(b"BBBB")
    (upload_temp_dir / f"{temp_file_id}.ov_upload.meta").write_text(
        json.dumps(
            {
                "original_filename": "integrity.bin",
                "size": 4,
                "sha256": hashlib.sha256(b"AAAA").hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    resolved = TempUploadStore(ServerConfig())._resolve_local(temp_file_id)

    assert resolved.source_sha256 == hashlib.sha256(b"BBBB").hexdigest()
    assert resolved.source_size == 4


async def test_resolve_shared_fingerprints_consumed_content(monkeypatch):
    stale_content = b"AAAA"
    consumed_content = b"BBBBB"
    meta = {
        "temp_file_id": "shared_upload-id",
        "account": "acct",
        "storage_uri": "viking://upload/upload-id/content",
        "state": "uploaded",
        "file_ext": ".bin",
        "original_filename": "integrity.bin",
        "size": len(stale_content),
        "sha256": hashlib.sha256(stale_content).hexdigest(),
    }

    class FakeVikingFS:
        async def exists(self, uri, *, ctx):
            return True

        def _uri_to_path(self, uri, *, ctx):
            return "/upload/upload-id"

        async def read_file_bytes(self, uri, *, ctx):
            return consumed_content

    class FakeLockManager:
        def create_handle(self):
            return object()

        async def acquire_tree(self, handle, path, *, timeout):
            return True

        async def release(self, handle):
            return None

    store = TempUploadStore(ServerConfig())

    async def read_meta(upload_id, ctx):
        return meta.copy()

    async def write_meta(upload_id, ctx, updated_meta):
        return None

    monkeypatch.setattr(store, "_read_shared_meta", read_meta)
    monkeypatch.setattr(store, "_write_shared_meta", write_meta)
    monkeypatch.setattr("openviking.server.temp_upload_store.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(
        "openviking.server.temp_upload_store.get_lock_manager", lambda: FakeLockManager()
    )
    ctx = RequestContext(user=UserIdentifier("acct", "user"), role=Role.USER)

    resolved = await store._resolve_shared("shared_upload-id", "upload-id", ctx)
    try:
        assert resolved.source_sha256 == hashlib.sha256(consumed_content).hexdigest()
        assert resolved.source_size == len(consumed_content)
    finally:
        await resolved.cleanup()
