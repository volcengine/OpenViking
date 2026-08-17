import json
import os
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


def test_local_upload_cleanup_is_disabled_when_ttl_is_zero(tmp_path, monkeypatch):
    now = 1_800_000_000
    expired_file = tmp_path / "expired.txt"
    expired_file.touch()
    os.utime(expired_file, (now - 61, now - 61))
    monkeypatch.setattr("openviking.server.temp_upload_store.time.time", lambda: now)

    store = TempUploadStore(ServerConfig(temp_upload={"ttl_seconds": 0}))
    store._cleanup_local_temp_files(tmp_path)

    assert expired_file.exists()


@pytest.mark.asyncio
async def test_shared_upload_cleanup_uses_timestamp_directories_in_one_listing(
    monkeypatch,
):
    now = 1_800_000_000
    old_upload = "1799999939000-11111111111111111111111111111111"
    current_upload = "1799999940000-22222222222222222222222222222222"
    malformed_upload = "not-a-timestamp"
    malformed_nonce_upload = "1799999939000-not-a-uuid"
    old_uri = f"viking://upload/{old_upload}"
    current_uri = f"viking://upload/{current_upload}"
    malformed_uri = f"viking://upload/{malformed_upload}"
    malformed_nonce_uri = f"viking://upload/{malformed_nonce_upload}"

    class FakeVikingFS:
        async def ls(self, uri, **kwargs):
            assert uri == "viking://upload"
            assert kwargs["ctx"].role == Role.ROOT
            return [
                {"uri": old_uri, "isDir": True},
                {"uri": current_uri, "isDir": True},
                {"uri": malformed_uri, "isDir": True},
                {"uri": malformed_nonce_uri, "isDir": True},
                {"uri": "viking://upload/ignore.txt", "isDir": False},
            ]

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
            (old_uri,),
            {"recursive": True, "ctx": RequestContext(user=ctx.user, role=Role.ROOT)},
        ),
    ]
    mock_logger.debug.assert_any_call(
        "Shared temp upload cleanup account=%s ttl_seconds=%s upload_count=%s now=%s cutoff=%s",
        "account",
        60,
        5,
        now,
        now - 60,
    )
    mock_logger.debug.assert_any_call(
        "Shared temp upload cleanup candidate uri=%s created_at=%s age_seconds=%s expired=%s",
        old_uri,
        1_799_999_939.0,
        61.0,
        True,
    )
    mock_logger.debug.assert_any_call("Shared temp upload cleanup removed uri=%s", old_uri)


@pytest.mark.asyncio
async def test_shared_upload_cleanup_is_disabled_when_ttl_is_zero(monkeypatch):
    class FakeVikingFS:
        ls = AsyncMock()
        rm = AsyncMock()

    fake_vfs = FakeVikingFS()
    monkeypatch.setattr("openviking.server.temp_upload_store.get_viking_fs", lambda: fake_vfs)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    store = TempUploadStore(ServerConfig(temp_upload={"ttl_seconds": 0}))

    await store._cleanup_shared_uploads(ctx)

    fake_vfs.ls.assert_not_awaited()
    fake_vfs.rm.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_deletion_removes_only_current_users_shared_uploads():
    target_upload = "1800000000000-target"
    other_user_upload = "1800000000000-other-user"
    other_account_upload = "1800000000000-other-account"
    target_uri = f"viking://upload/{target_upload}"
    target_meta = f"{target_uri}/meta"
    other_user_meta = f"viking://upload/{other_user_upload}/meta"
    other_account_meta = f"viking://upload/{other_account_upload}/meta"

    class FakeVikingFS:
        async def ls(self, uri, **kwargs):
            assert uri == "viking://upload"
            return [
                {"uri": target_uri, "isDir": True},
                {"uri": f"viking://upload/{other_user_upload}", "isDir": True},
                {"uri": f"viking://upload/{other_account_upload}", "isDir": True},
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
        ((target_uri,), {"recursive": True, "ctx": ctx}),
    ]
