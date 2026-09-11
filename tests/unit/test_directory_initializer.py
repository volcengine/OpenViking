from unittest.mock import AsyncMock

import pytest

from openviking.core.directories import PRESET_DIRECTORIES, DirectoryInitializer
from openviking.core.namespace import canonical_user_root, may_include_hidden_actor_peers
from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import body_for_preview
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingDB:
    def __init__(self):
        self.embedding_messages = []

    async def get_context_by_uri(self, **_kwargs):
        return []

    async def enqueue_embedding_msg(self, message):
        self.embedding_messages.append(message)


class _FakeVikingFS:
    def __init__(self):
        self.contexts = {}

    async def abstract(self, uri, ctx):
        if uri not in self.contexts:
            raise FileNotFoundError(uri)
        return body_for_preview(self.contexts[uri]["abstract"])

    async def write_context(self, uri, abstract, overview, is_leaf, ctx):
        if ctx.actor_peer_id and may_include_hidden_actor_peers(uri, ctx):
            raise PermissionError(f"actor peer cannot mutate {uri}")
        self.contexts[uri] = {
            "abstract": abstract,
            "overview": overview,
            "is_leaf": is_leaf,
        }


@pytest.mark.asyncio
async def test_initialize_preset_directories_creates_roots_and_first_level_only(monkeypatch):
    vikingdb = _FakeVikingDB()
    viking_fs = _FakeVikingFS()
    initializer = DirectoryInitializer(vikingdb, viking_fs=viking_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)

    account_count = await initializer.initialize_account_directories(ctx)
    count = await initializer.initialize_user_directories(ctx)

    user_root = canonical_user_root(ctx)
    expected_uris = {
        "viking://resources",
        user_root,
        *(f"{user_root}/{child.path}" for child in PRESET_DIRECTORIES["user"].children),
    }
    assert account_count == 1
    assert count == len(expected_uris) - 1
    assert set(viking_fs.contexts) == expected_uris
    assert f"{user_root}/memories/preferences" not in viking_fs.contexts

    for uri in ("viking://resources", user_root):
        viking_fs.contexts[uri]["abstract"] = f"---\ndirectory: {uri}/\n"
    embedding_count = len(vikingdb.embedding_messages)

    second_account_count = await initializer.initialize_account_directories(ctx)
    second_count = await initializer.initialize_user_directories(ctx)

    assert (second_account_count, second_count) == (0, 0)
    for uri in ("viking://resources", user_root):
        assert viking_fs.contexts[uri]["abstract"] == f"---\ndirectory: {uri}/\n"
    assert set(viking_fs.contexts) == expected_uris
    assert len(vikingdb.embedding_messages) == embedding_count

    storage_error = PermissionError("storage access denied")
    monkeypatch.setattr(viking_fs, "abstract", AsyncMock(side_effect=storage_error))
    for initialize in (
        initializer.initialize_account_directories,
        initializer.initialize_user_directories,
    ):
        with pytest.raises(PermissionError) as exc_info:
            await initialize(ctx)
        assert exc_info.value is storage_error


@pytest.mark.asyncio
async def test_initialize_user_directories_ignores_actor_peer_view_for_preset_structure():
    vikingdb = _FakeVikingDB()
    viking_fs = _FakeVikingFS()
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
    assert set(viking_fs.contexts) == expected_uris
