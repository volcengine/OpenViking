import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from openviking.core.directories import PRESET_DIRECTORIES, DirectoryInitializer
from openviking.core.namespace import (
    canonical_user_root,
    is_session_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.storage.abstract_overview import body_for_preview
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


class _FakeVikingDB:
    def __init__(self):
        self.embedding_messages = []
        self.get_calls = []

    async def get(self, ids, ctx):
        self.get_calls.append((ids, ctx))
        return []

    async def enqueue_embedding_msg(self, message):
        self.embedding_messages.append(message)


@pytest.fixture
def viking_fs(tmp_path):
    return VikingFS(agfs=MockLocalAGFS(root_path=tmp_path))


@pytest.mark.asyncio
async def test_initialize_account_workspace_batches_preset_directories(monkeypatch, viking_fs):
    vikingdb = _FakeVikingDB()
    initializer = DirectoryInitializer(vikingdb, viking_fs=viking_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)
    await viking_fs.write_file(
        "viking://agent/tools/.abstract.md", "Existing tool configuration", ctx=ctx
    )

    account_count, user_count = await initializer.initialize_account_workspace(ctx)

    user_root = canonical_user_root(ctx)
    expected_user_uris = {
        user_root,
        *(f"{user_root}/{child.path}" for child in PRESET_DIRECTORIES["user"].children),
    }
    expected_agent_uris = {
        "viking://agent",
        "viking://agent/skills",
        "viking://agent/endpoints",
        "viking://agent/tools",
        "viking://agent/payments",
    }
    expected_uris = {"viking://resources", *expected_agent_uris, *expected_user_uris}
    assert account_count == 6
    assert user_count == len(expected_user_uris)
    for uri in expected_uris:
        assert await viking_fs.read_file(f"{uri}/.abstract.md", ctx=ctx)
        assert await viking_fs.read_file(f"{uri}/.overview.md", ctx=ctx)
    assert not viking_fs.agfs.exists("/local/acme/user/alice/memories/preferences")
    assert await viking_fs.read_file("viking://agent/tools/.abstract.md", ctx=ctx) == (
        "Existing tool configuration"
    )
    assert len(vikingdb.get_calls) == 1

    assert await initializer.initialize_account_directories(ctx) == 0
    vectorized_uris = {uri for uri in expected_uris if not is_session_uri(uri)}
    assert len(vikingdb.get_calls[0][0]) == 2 * (1 + len(expected_agent_uris))
    assert len(vikingdb.embedding_messages) == 2 * len(vectorized_uris)
    for message in vikingdb.embedding_messages:
        uri = message.context_data["uri"]
        if uri in expected_agent_uris:
            name = "abstract" if message.context_data["level"] == 0 else "overview"
            assert message.message == body_for_preview(
                await viking_fs.read_file(f"{uri}/.{name}.md", ctx=ctx)
            )
            assert message.context_data["parent_uri"] == (
                None if uri == "viking://agent" else "viking://agent"
            )

    malformed_abstract = "---\ndirectory: viking://resources/\n"
    await viking_fs.write_file("viking://resources/.abstract.md", malformed_abstract, ctx=ctx)

    second_account_count, second_user_count = await initializer.initialize_account_workspace(ctx)

    assert (second_account_count, second_user_count) == (0, 0)
    assert (
        await viking_fs.read_file("viking://resources/.abstract.md", ctx=ctx) == malformed_abstract
    )
    assert len(vikingdb.get_calls) == 1

    storage_error = PermissionError("storage access denied")
    monkeypatch.setattr(viking_fs, "read_file", AsyncMock(side_effect=storage_error))
    with pytest.raises(PermissionError) as exc_info:
        await initializer.initialize_account_workspace(ctx)
    assert exc_info.value is storage_error


@pytest.mark.asyncio
async def test_initialize_user_directories_ignores_actor_peer_view_for_preset_structure(
    monkeypatch, viking_fs
):
    vikingdb = _FakeVikingDB()
    initializer = DirectoryInitializer(vikingdb, viking_fs=viking_fs)
    ctx = RequestContext(
        user=UserIdentifier("acme", "support-bot"),
        role=Role.USER,
        actor_peer_id="customer-a",
    )

    agfs = viking_fs._async_agfs
    acquire = AsyncMock(wraps=agfs.pathlock_acquire_tree)
    release = AsyncMock(wraps=agfs.pathlock_release)
    monkeypatch.setattr(agfs, "pathlock_acquire_tree", acquire)
    monkeypatch.setattr(agfs, "pathlock_release", release)
    monkeypatch.setattr(
        viking_fs, "read_file", AsyncMock(side_effect=AssertionError("preset read"))
    )
    sanitizer = SimpleNamespace(sanitize=AsyncMock(side_effect=AssertionError("preset sanitize")))
    viking_fs.set_privacy_sanitizer(sanitizer)

    count = await initializer.initialize_user_directories(ctx)

    user_root = canonical_user_root(ctx)
    expected_uris = {
        user_root,
        *(f"{user_root}/{child.path}" for child in PRESET_DIRECTORIES["user"].children),
    }
    assert count == len(expected_uris)
    acquire.assert_awaited_once_with("/local/acme/user/support-bot")
    release.assert_awaited_once()
    assert vikingdb.get_calls == []
    assert len(vikingdb.embedding_messages) == 12
    assert {message.context_data["uri"] for message in vikingdb.embedding_messages} == (
        expected_uris - {f"{user_root}/sessions"}
    )
    for uri in expected_uris:
        for name in ("abstract", "overview"):
            raw = await agfs.read(viking_fs._uri_to_path(f"{uri}/.{name}.md", ctx=ctx))
            assert body_for_preview(raw.decode())

    existing_file = "/local/acme/user/support-bot/memories/.abstract.md"
    await agfs.write(existing_file, b"Existing user content")
    await agfs.rm("/local/acme/user/support-bot/skills/.overview.md")
    assert await initializer.initialize_user_directories(ctx) == 0
    assert await agfs.read(existing_file) == b"Existing user content"
    assert not viking_fs.agfs.exists("/local/acme/user/support-bot/skills/.overview.md")
    assert len(vikingdb.embedding_messages) == 12

    for index, error in enumerate((OSError("storage write failed"), asyncio.CancelledError())):
        failing_ctx = RequestContext(user=UserIdentifier("acme", f"failed-{index}"), role=Role.USER)
        with monkeypatch.context() as patch:
            patch.setattr(agfs, "write", AsyncMock(side_effect=error))
            with pytest.raises(type(error)):
                await initializer.initialize_user_directories(failing_ctx)
        assert viking_fs.agfs.exists(f"/local/acme/user/failed-{index}")
        assert len(vikingdb.embedding_messages) == 12
    assert release.await_count == acquire.await_count == 3


@pytest.mark.asyncio
async def test_system_assets_sync_only_for_account_initialization(monkeypatch, viking_fs):
    synchronizer = SimpleNamespace(sync=AsyncMock())
    agfs = viking_fs._async_agfs
    acquire = AsyncMock(wraps=agfs.pathlock_acquire_tree)
    release = AsyncMock(wraps=agfs.pathlock_release)
    monkeypatch.setattr(agfs, "pathlock_acquire_tree", acquire)
    monkeypatch.setattr(agfs, "pathlock_release", release)
    initializer = DirectoryInitializer(
        _FakeVikingDB(),
        viking_fs=viking_fs,
        system_asset_synchronizer=synchronizer,
    )
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._viking_fs = viking_fs
    service._directory_initializer = initializer
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)

    await initializer.initialize_account_workspace(ctx, sync_system_assets=False)
    synchronizer.sync.assert_not_awaited()
    acquire.assert_awaited_once_with("/local/acme/user/alice")

    await agfs.rm("/local/acme", recursive=True)
    acquire.reset_mock()
    release.reset_mock()
    assert await service.initialize_account_workspace(ctx) == (6, 7)
    acquire.assert_awaited_once_with("/local/acme")
    lease = synchronizer.sync.await_args.kwargs["lease_ref"]
    release.assert_awaited_once_with(lease)
    await initializer.initialize_account_directories(ctx)
    await initializer.initialize_user_directories(ctx)

    assert synchronizer.sync.await_args_list == [call(ctx, lease_ref=lease), call(ctx)]

    for error in (OSError("asset write failed"), asyncio.CancelledError()):
        synchronizer.sync.side_effect = error
        with pytest.raises(type(error)):
            await service.initialize_account_workspace(ctx)
    assert release.await_count == acquire.await_count == 3


def test_preset_directory_uris_cover_seeded_directories_only():
    from openviking.core.directories import preset_directory_uris

    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    user_root = canonical_user_root(ctx)

    uris = preset_directory_uris(ctx)

    assert {
        "viking://resources",
        "viking://agent",
        "viking://agent/skills",
        user_root,
        f"{user_root}/memories",
        f"{user_root}/resources",
        f"{user_root}/privacy",
        f"{user_root}/peers",
    } <= uris
    assert f"{user_root}/memories/preferences" not in uris
    assert "viking://resources/proj" not in uris
