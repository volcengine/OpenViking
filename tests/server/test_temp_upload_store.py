import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.temp_upload_store import TempUploadStore
from openviking.service.user_deletion import UserDeletionService
from openviking_cli.session.user_id import UserIdentifier


def test_local_upload_cleanup_uses_configured_ttl(tmp_path, monkeypatch):
    now = 1_800_000_000
    expired_file = tmp_path / "expired.txt"
    current_file = tmp_path / "current.txt"
    expired_file.touch()
    current_file.touch()
    os.utime(expired_file, (now - 61, now - 61))
    os.utime(current_file, (now - 60, now - 60))
    monkeypatch.setattr("openviking.server.temp_upload_store.time.time", lambda: now)

    store = TempUploadStore(ServerConfig(temp_upload={"ttl_seconds": 60}))
    store._cleanup_local_temp_files(tmp_path)

    assert not expired_file.exists()
    assert current_file.exists()


@pytest.mark.asyncio
async def test_shared_upload_cleanup_uses_flat_file_mtimes_in_one_listing(
    monkeypatch,
):
    now = 1_800_000_000
    old_content = "viking://upload/old.content"
    old_meta = "viking://upload/old.meta.json"
    current_content = "viking://upload/current.content"
    current_meta = "viking://upload/current.meta.json"
    orphan_content = "viking://upload/orphan.content"

    class FakeVikingFS:
        async def ls(self, uri, **kwargs):
            assert uri == "viking://upload"
            assert kwargs["ctx"].role == Role.ROOT
            return [
                {"uri": old_content, "isDir": False, "modTime": now - 120},
                {"uri": old_meta, "isDir": False, "modTime": now - 61},
                {"uri": current_content, "isDir": False, "modTime": now - 120},
                {"uri": current_meta, "isDir": False, "modTime": now - 60},
                {"uri": orphan_content, "isDir": False, "modTime": now - 61},
                {"uri": "viking://upload/ignore.txt", "isDir": False, "modTime": now - 61},
            ]

        @staticmethod
        def _ls_entry_mtime(entry):
            return entry.get("modTime")

        rm = AsyncMock()

    fake_vfs = FakeVikingFS()
    monkeypatch.setattr("openviking.server.temp_upload_store.get_viking_fs", lambda: fake_vfs)
    monkeypatch.setattr("openviking.server.temp_upload_store.time.time", lambda: now)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    server_config = ServerConfig(temp_upload={"ttl_seconds": 60})

    with patch("openviking.server.temp_upload_store.logger") as mock_logger:
        await TempUploadStore(server_config)._cleanup_shared_uploads(ctx)

    assert fake_vfs.rm.await_args_list == [
        (
            (old_content,),
            {"recursive": False, "ctx": RequestContext(user=ctx.user, role=Role.ROOT)},
        ),
        (
            (old_meta,),
            {"recursive": False, "ctx": RequestContext(user=ctx.user, role=Role.ROOT)},
        ),
        (
            (orphan_content,),
            {"recursive": False, "ctx": RequestContext(user=ctx.user, role=Role.ROOT)},
        ),
    ]
    mock_logger.debug.assert_any_call(
        "Shared temp upload cleanup account=%s ttl_seconds=%s upload_count=%s now=%s cutoff=%s",
        "account",
        60,
        6,
        now,
        now - 60,
    )
    mock_logger.debug.assert_any_call(
        "Shared temp upload cleanup candidate uri=%s mod_time=%s age_seconds=%s expired=%s",
        old_meta,
        now - 61,
        61.0,
        True,
    )
    mock_logger.debug.assert_any_call("Shared temp upload cleanup removed uri=%s", old_meta)


@pytest.mark.asyncio
async def test_shared_upload_resolves_legacy_directory_layout(monkeypatch):
    upload_id = "legacy"
    temp_file_id = f"shared_{upload_id}"
    legacy_content = f"viking://upload/{upload_id}/content"
    legacy_meta = f"viking://upload/{upload_id}/meta.json"

    class FakeVikingFS:
        async def read_file(self, uri, **kwargs):
            assert uri == legacy_meta
            return json.dumps(
                {
                    "temp_file_id": temp_file_id,
                    "account": "account",
                    "storage_uri": legacy_content,
                    "file_ext": ".txt",
                }
            )

        async def exists(self, uri, **kwargs):
            return uri == legacy_content

        async def read_file_bytes(self, uri, **kwargs):
            assert uri == legacy_content
            return b"legacy upload"

    fake_vfs = FakeVikingFS()
    monkeypatch.setattr("openviking.server.temp_upload_store.get_viking_fs", lambda: fake_vfs)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)

    resolved = await TempUploadStore(ServerConfig()).resolve_for_consume(temp_file_id, ctx)

    try:
        assert Path(resolved.local_path).read_bytes() == b"legacy upload"
    finally:
        await resolved.cleanup()


@pytest.mark.asyncio
async def test_user_deletion_removes_only_current_users_shared_uploads():
    target_meta = "viking://upload/target.meta.json"
    target_content = "viking://upload/target.content"
    other_user_meta = "viking://upload/other-user.meta.json"
    other_account_meta = "viking://upload/other-account.meta.json"

    class FakeVikingFS:
        async def ls(self, uri, **kwargs):
            assert uri == "viking://upload"
            return [
                {"uri": target_meta, "isDir": False},
                {"uri": target_content, "isDir": False},
                {"uri": other_user_meta, "isDir": False},
                {"uri": other_account_meta, "isDir": False},
            ]

        async def read_file(self, uri, **kwargs):
            metadata = {
                target_meta: {"account": "account", "user": "user"},
                other_user_meta: {"account": "account", "user": "other"},
                other_account_meta: {"account": "other", "user": "user"},
            }
            return json.dumps(metadata[uri])

        rm = AsyncMock()

    fake_vfs = FakeVikingFS()
    service = SimpleNamespace(viking_fs=fake_vfs)
    deletion_service = UserDeletionService(
        service=service,
        manager=object(),
        service_loop=object(),
    )
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)

    await deletion_service._delete_uploads(ctx)

    assert fake_vfs.rm.await_args_list == [
        ((target_content,), {"ctx": ctx}),
        ((target_meta,), {"recursive": True, "ctx": ctx}),
    ]
