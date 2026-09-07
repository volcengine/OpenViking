import pytest

from openviking.core.directories import PRESET_DIRECTORIES, DirectoryInitializer
from openviking.core.namespace import (
    canonical_user_root,
    is_session_uri,
    may_include_hidden_actor_peers,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


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

    async def abstract(self, uri, ctx):
        if uri not in self.contexts:
            raise FileNotFoundError(uri)
        return self.contexts[uri]["abstract"]

    async def write_context(self, uri, abstract, overview, is_leaf, ctx):
        if ctx.actor_peer_id and may_include_hidden_actor_peers(uri, ctx):
            raise PermissionError(f"actor peer cannot mutate {uri}")
        self.contexts[uri] = {
            "abstract": abstract,
            "overview": overview,
            "is_leaf": is_leaf,
        }


@pytest.mark.asyncio
async def test_initialize_account_workspace_batches_preset_directories():
    vikingdb = _FakeVikingDB()
    viking_fs = _FakeVikingFS()
    initializer = DirectoryInitializer(vikingdb, viking_fs=viking_fs)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)

    account_count, user_count = await initializer.initialize_account_workspace(ctx)

    user_root = canonical_user_root(ctx)
    expected_user_uris = {
        user_root,
        *(f"{user_root}/{child.path}" for child in PRESET_DIRECTORIES["user"].children),
    }
    expected_uris = {"viking://resources", *expected_user_uris}
    assert account_count == 1
    assert user_count == len(expected_user_uris)
    assert set(viking_fs.contexts) == expected_uris
    assert f"{user_root}/memories/preferences" not in viking_fs.contexts
    assert len(vikingdb.get_calls) == 1
    vectorized_uris = {uri for uri in expected_uris if not is_session_uri(uri)}
    assert len(vikingdb.get_calls[0][0]) == 2 * len(vectorized_uris)
    assert len(vikingdb.embedding_messages) == 2 * len(vectorized_uris)

    second_account_count, second_user_count = await initializer.initialize_account_workspace(ctx)

    assert (second_account_count, second_user_count) == (0, 0)
    assert set(viking_fs.contexts) == expected_uris
    assert len(vikingdb.get_calls) == 1


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
