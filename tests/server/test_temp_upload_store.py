import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.temp_upload_store import TempUploadStore
from openviking.service.user_deletion import UserDeletionService
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_shared_upload_cleanup_removes_only_expired_directories(monkeypatch):
    now = 1_800_000_000
    old_upload = "viking://upload/old"
    current_upload = "viking://upload/current"

    class FakeVikingFS:
        async def ls(self, uri, **kwargs):
            assert uri == "viking://upload"
            assert kwargs["ctx"].role == Role.ROOT
            return [
                {"uri": old_upload, "isDir": True, "modTime": now - 61},
                {"uri": current_upload, "isDir": True, "modTime": now - 60},
                {"uri": "viking://upload/unknown", "isDir": True},
                {"uri": "viking://upload/file", "isDir": False, "modTime": now - 61},
            ]

        @staticmethod
        def _ls_entry_mtime(entry):
            return entry.get("modTime")

        rm = AsyncMock()

    fake_vfs = FakeVikingFS()
    monkeypatch.setattr("openviking.server.temp_upload_store.get_viking_fs", lambda: fake_vfs)
    monkeypatch.setattr("openviking.server.temp_upload_store.time.time", lambda: now)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    server_config = ServerConfig(temp_upload={"shared_ttl_seconds": 60})

    await TempUploadStore(server_config)._cleanup_shared_uploads(ctx)

    fake_vfs.rm.assert_awaited_once_with(
        old_upload,
        recursive=True,
        ctx=RequestContext(user=ctx.user, role=Role.ROOT),
    )


@pytest.mark.asyncio
async def test_user_deletion_removes_only_current_users_shared_uploads():
    target_upload = "viking://upload/target"
    other_user_upload = "viking://upload/other-user"
    other_account_upload = "viking://upload/other-account"

    class FakeVikingFS:
        async def ls(self, uri, **kwargs):
            assert uri == "viking://upload"
            return [
                {"uri": target_upload, "isDir": True},
                {"uri": other_user_upload, "isDir": True},
                {"uri": other_account_upload, "isDir": True},
            ]

        async def read_file(self, uri, **kwargs):
            metadata = {
                f"{target_upload}/meta.json": {"account": "account", "user": "user"},
                f"{other_user_upload}/meta.json": {"account": "account", "user": "other"},
                f"{other_account_upload}/meta.json": {"account": "other", "user": "user"},
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

    fake_vfs.rm.assert_awaited_once_with(target_upload, recursive=True, ctx=ctx)
