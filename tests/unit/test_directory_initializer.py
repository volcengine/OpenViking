import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from openviking.core.directories import PRESET_DIRECTORIES, DirectoryInitializer
from openviking.core.namespace import (
    canonical_user_root,
    is_session_uri,
    may_include_hidden_actor_peers,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.viking_fs import VikingFS
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking_cli.exceptions import FailedPreconditionError, PermissionDeniedError
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


class _FakeVikingFS:
    def __init__(self):
        self.contexts = {}
        self.write_leases = []
        self.files = {}
        self._async_agfs = SimpleNamespace(
            pathlock_acquire_tree=AsyncMock(return_value={"lease_ref": "agent-init"}),
            pathlock_release=AsyncMock(),
        )

    def _uri_to_path(self, uri, ctx=None):
        return "/local/acme/" + uri.removeprefix("viking://")

    async def read_file(self, uri, ctx):
        if uri in self.files:
            return self.files[uri]
        directory, filename = uri.rsplit("/", 1)
        name = filename.removeprefix(".").removesuffix(".md")
        if directory not in self.contexts or name not in self.contexts[directory]:
            raise FileNotFoundError(uri)
        return self.contexts[directory][name]

    async def write_file(self, uri, content, **kwargs):
        self.files[uri] = content

    async def write_context(
        self, uri, abstract="", overview="", is_leaf=False, ctx=None, *, lease_ref=None
    ):
        if ctx.actor_peer_id and may_include_hidden_actor_peers(uri, ctx):
            raise PermissionError(f"actor peer cannot mutate {uri}")
        context = self.contexts.setdefault(uri, {})
        if abstract:
            context["abstract"] = abstract
        if overview:
            context["overview"] = overview
        context["is_leaf"] = is_leaf
        self.write_leases.append(lease_ref)


@pytest.mark.asyncio
async def test_initialize_account_workspace_batches_preset_directories(monkeypatch):
    vikingdb = _FakeVikingDB()
    viking_fs = _FakeVikingFS()
    viking_fs.contexts["viking://agent"] = {}
    viking_fs.contexts["viking://agent/tools"] = {"abstract": "Existing tool configuration"}
    initializer = DirectoryInitializer(vikingdb, viking_fs=viking_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)
    memory_root = f"{canonical_user_root(ctx)}/memories"
    legacy_files = {f"{memory_root}/{name}.md": f"Old {name}" for name in ("soul", "identity")}
    viking_fs.files.update(legacy_files)

    account_count, user_count = await initializer.initialize_account_workspace(ctx)
    assert viking_fs.files == {
        **legacy_files,
        f"{memory_root}/SOUL.md": (SYSTEM_ASSETS_DIR / "memory" / "SOUL.md").read_text(),
    }
    viking_fs.files[f"{memory_root}/SOUL.md"] = "Custom agent instructions"

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
    assert set(viking_fs.contexts) == expected_uris
    assert f"{user_root}/memories/preferences" not in viking_fs.contexts
    for uri in expected_agent_uris:
        assert viking_fs.contexts[uri]["abstract"]
        assert viking_fs.contexts[uri]["overview"]
    assert viking_fs.contexts["viking://agent/tools"]["abstract"] == "Existing tool configuration"
    assert len(vikingdb.get_calls) == 1

    assert await initializer.initialize_account_directories(ctx) == 0
    assert set(viking_fs.contexts) == expected_uris
    vectorized_uris = {uri for uri in expected_uris if not is_session_uri(uri)}
    assert len(vikingdb.get_calls[0][0]) == 2 * len(vectorized_uris)
    assert len(vikingdb.embedding_messages) == 2 * len(vectorized_uris)
    for message in vikingdb.embedding_messages:
        uri = message.context_data["uri"]
        if uri in expected_agent_uris:
            name = "abstract" if message.context_data["level"] == 0 else "overview"
            assert message.message == viking_fs.contexts[uri][name]
            assert message.context_data["parent_uri"] == (
                None if uri == "viking://agent" else "viking://agent"
            )

    malformed_abstract = "---\ndirectory: viking://resources/\n"
    viking_fs.contexts["viking://resources"]["abstract"] = malformed_abstract

    second_account_count, second_user_count = await initializer.initialize_account_workspace(ctx)

    assert (second_account_count, second_user_count) == (0, 0)
    assert viking_fs.files == {
        **legacy_files,
        f"{memory_root}/SOUL.md": "Custom agent instructions",
    }
    assert viking_fs.contexts["viking://resources"]["abstract"] == malformed_abstract
    assert set(viking_fs.contexts) == expected_uris
    assert len(vikingdb.get_calls) == 1

    storage_error = PermissionError("storage access denied")
    monkeypatch.setattr(viking_fs, "read_file", AsyncMock(side_effect=storage_error))
    with pytest.raises(PermissionError) as exc_info:
        await initializer.initialize_account_workspace(ctx)
    assert exc_info.value is storage_error


@pytest.mark.asyncio
async def test_initialize_user_directories_ignores_actor_peer_view_for_preset_structure(tmp_path):
    vikingdb = _FakeVikingDB()
    agfs = MockLocalAGFS(root_path=tmp_path)
    viking_fs = VikingFS(agfs)
    prepare_parent = AsyncMock(wraps=viking_fs._async_agfs.ensure_parent_dirs)
    viking_fs._async_agfs.ensure_parent_dirs = prepare_parent
    deletion_guard = AsyncMock(return_value=False)
    viking_fs.set_deletion_guard(deletion_guard)
    sanitizer = SimpleNamespace(
        sanitize=AsyncMock(
            side_effect=lambda content, **kwargs: SimpleNamespace(
                content=content.replace("User scope.", "Sanitized scope."),
                ambiguous_references=(),
            )
        )
    )
    viking_fs.set_privacy_sanitizer(sanitizer)
    initializer = DirectoryInitializer(vikingdb, viking_fs=viking_fs)
    ctx = RequestContext(
        user=UserIdentifier("acme", "support-bot"),
        role=Role.USER,
        actor_peer_id="customer-a",
    )

    count = await initializer.initialize_user_directories(ctx)

    user_root = canonical_user_root(ctx)
    expected_uris = {
        user_root,
        *(f"{user_root}/{child.path}" for child in PRESET_DIRECTORIES["user"].children),
    }
    assert count == len(expected_uris)
    assert deletion_guard.await_count == len(expected_uris) + 1
    assert prepare_parent.await_count == len(expected_uris) + 1
    root_path = agfs._resolve(viking_fs._uri_to_path(user_root, ctx=ctx))
    assert {str(path.relative_to(root_path)) for path in root_path.rglob("*.md")} == {
        f"{uri.removeprefix(user_root + '/')}/{filename}" if uri != user_root else filename
        for uri in expected_uris
        for filename in (".abstract.md", ".overview.md")
    } | {"memories/SOUL.md"}
    assert "Sanitized scope." in (root_path / ".abstract.md").read_text()

    with pytest.raises(PermissionDeniedError):
        await viking_fs.write_context(user_root, abstract="blocked", ctx=ctx)
    deletion_guard.return_value = True
    with pytest.raises(FailedPreconditionError):
        await initializer.initialize_user_directories(
            RequestContext(user=UserIdentifier("acme", "deleted-user"), role=Role.USER)
        )
    assert not agfs._resolve("/local/acme/user/deleted-user").exists()


@pytest.mark.asyncio
async def test_system_assets_sync_only_for_account_initialization(monkeypatch):
    synchronizer = SimpleNamespace(sync=AsyncMock())
    viking_fs = _FakeVikingFS()
    lease = {"lease_ref": "account-init"}
    agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value=lease),
        pathlock_release=AsyncMock(),
    )
    viking_fs._async_agfs = agfs
    viking_fs._uri_to_path = lambda uri, ctx: (
        f"/local/{ctx.account_id}/{uri.removeprefix('viking://')}".rstrip("/")
    )
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
    assert all(value is None for value in viking_fs.write_leases)
    agfs.pathlock_acquire_tree.reset_mock()
    agfs.pathlock_release.reset_mock()

    viking_fs.contexts.clear()
    viking_fs.write_leases.clear()
    user_count = 1 + len(PRESET_DIRECTORIES["user"].children)
    assert await service.initialize_account_workspace(ctx) == (6, user_count)
    agfs.pathlock_acquire_tree.assert_awaited_once_with("/local/acme", timeout_secs=10.0)
    agfs.pathlock_release.assert_awaited_once_with(lease)
    assert viking_fs.write_leases == [lease] * (6 + user_count)
    await initializer.initialize_account_directories(ctx)
    await initializer.initialize_user_directories(ctx)

    assert synchronizer.sync.await_args_list == [call(ctx, lease_ref=lease), call(ctx)]

    agfs.pathlock_acquire_tree.reset_mock()
    agfs.pathlock_release.reset_mock()
    viking_fs.contexts.clear()
    viking_fs.write_leases.clear()
    assert await service.initialize_account_workspace(ctx, lease_ref=lease) == (6, user_count)
    agfs.pathlock_acquire_tree.assert_not_awaited()
    agfs.pathlock_release.assert_not_awaited()
    assert viking_fs.write_leases == [lease] * (6 + user_count)

    for error in (OSError("asset write failed"), asyncio.CancelledError()):
        synchronizer.sync.side_effect = error
        with pytest.raises(type(error)):
            await service.initialize_account_workspace(ctx)
    assert agfs.pathlock_release.await_args_list == [call(lease)] * 2

    viking_fs.contexts.clear()
    viking_fs.write_leases.clear()
    agfs.pathlock_acquire_tree.reset_mock()
    agfs.pathlock_release.reset_mock()
    assert await service.initialize_user_directories(ctx) == user_count
    agfs.pathlock_acquire_tree.assert_awaited_once_with("/local/acme/user/alice")
    assert viking_fs.write_leases == [lease] * user_count
    agfs.pathlock_release.assert_awaited_once_with(lease)

    for error in (OSError("directory read failed"), asyncio.CancelledError()):
        monkeypatch.setattr(viking_fs, "read_file", AsyncMock(side_effect=error))
        with pytest.raises(type(error)):
            await service.initialize_user_directories(ctx)
    assert agfs.pathlock_release.await_args_list == [call(lease)] * 3

    agfs.pathlock_acquire_tree.side_effect = LockAcquisitionError("busy")
    with pytest.raises(ResourceBusyError) as exc_info:
        await service.initialize_user_directories(ctx)
    assert exc_info.value.uri == "viking://user/alice"
    assert agfs.pathlock_release.await_count == 3


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
