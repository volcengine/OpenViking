# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for temp-scope access control."""

from datetime import datetime, timezone

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class FakeAGFS:
    def __init__(self):
        self.dirs = {"/", "/local"}
        self.files = {}

    def mkdir(self, path):
        if path in self.files:
            raise FileExistsError(path)
        if path in self.dirs:
            raise FileExistsError(f"already exists: {path}")
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.dirs.add(path)
        return path

    def write(self, path, data):
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.files[path] = bytes(data)
        return path

    def rm(self, path, recursive=False):
        if path in self.files:
            del self.files[path]
            return {}
        if path not in self.dirs:
            raise FileNotFoundError(path)

        prefix = f"{path.rstrip('/')}/"
        has_children = any(
            item.startswith(prefix) for item in self.dirs | set(self.files) if item != path
        )
        if has_children and not recursive:
            raise OSError(f"directory not empty: {path}")

        for file_path in list(self.files):
            if file_path.startswith(prefix):
                del self.files[file_path]
        for dir_path in sorted(self.dirs, reverse=True):
            if dir_path.startswith(prefix):
                self.dirs.remove(dir_path)
        if path != "/":
            self.dirs.remove(path)
        return {}

    def read(self, path, offset=0, size=-1):
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        return data[offset:] if size == -1 else data[offset : offset + size]

    def stat(self, path):
        if path in self.dirs:
            return {"name": path.rsplit("/", 1)[-1] or "/", "isDir": True, "size": 0}
        if path in self.files:
            return {"name": path.rsplit("/", 1)[-1], "isDir": False, "size": len(self.files[path])}
        raise FileNotFoundError(path)

    def ls(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        children = {}
        prefix = path.rstrip("/")
        prefix = "" if prefix == "/" else prefix
        for d in sorted(self.dirs):
            if d in {"/", path}:
                continue
            if not d.startswith(prefix + "/"):
                continue
            remainder = d[len(prefix + "/") :] if prefix else d[1:]
            if "/" in remainder or not remainder:
                continue
            children[remainder] = {
                "name": remainder,
                "isDir": True,
                "size": 0,
                "modTime": datetime.now(timezone.utc).isoformat(),
            }
        for f, content in sorted(self.files.items()):
            if not f.startswith(prefix + "/"):
                continue
            remainder = f[len(prefix + "/") :] if prefix else f[1:]
            if "/" in remainder or not remainder:
                continue
            children[remainder] = {
                "name": remainder,
                "isDir": False,
                "size": len(content),
                "modTime": datetime.now(timezone.utc).isoformat(),
            }
        return list(children.values())


@pytest.fixture
def viking_fs():
    return VikingFS(agfs=FakeAGFS())


@pytest.mark.asyncio
async def test_temp_scope_isolated_between_users_in_same_account(viking_fs):
    owner_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )
    other_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob", agent_id="agent2"),
        role=Role.USER,
    )

    temp_uri = viking_fs.create_temp_uri(ctx=owner_ctx)
    secret_uri = f"{temp_uri}/secret.txt"

    await viking_fs.mkdir(temp_uri, exist_ok=True, ctx=owner_ctx)
    await viking_fs.write(secret_uri, "owner secret", ctx=owner_ctx)

    assert (await viking_fs.read(secret_uri, ctx=owner_ctx)).decode("utf-8") == "owner secret"

    with pytest.raises(PermissionError):
        await viking_fs.read(secret_uri, ctx=other_ctx)

    with pytest.raises(PermissionError):
        await viking_fs.write(secret_uri, "tampered", ctx=other_ctx)


@pytest.mark.asyncio
async def test_temp_scope_user_id_matching_legacy_pattern_stays_isolated(viking_fs):
    owner_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="04011234_abcdef", agent_id="agent1"),
        role=Role.USER,
    )
    other_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob", agent_id="agent2"),
        role=Role.USER,
    )

    temp_uri = viking_fs.create_temp_uri(ctx=owner_ctx)
    secret_uri = f"{temp_uri}/secret.txt"

    await viking_fs.mkdir(temp_uri, exist_ok=True, ctx=owner_ctx)
    await viking_fs.write(secret_uri, "owner secret", ctx=owner_ctx)

    assert temp_uri.startswith("viking://temp/04011234_abcdef/")
    with pytest.raises(PermissionError):
        await viking_fs.read(secret_uri, ctx=other_ctx)


@pytest.mark.asyncio
async def test_temp_root_listing_only_shows_callers_own_entries(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob", agent_id="agent2"),
        role=Role.USER,
    )

    alice_temp_uri = viking_fs.create_temp_uri(ctx=alice_ctx)
    bob_temp_uri = viking_fs.create_temp_uri(ctx=bob_ctx)

    await viking_fs.mkdir(alice_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.write(f"{alice_temp_uri}/alice.txt", "alice", ctx=alice_ctx)

    await viking_fs.mkdir(bob_temp_uri, exist_ok=True, ctx=bob_ctx)
    await viking_fs.write(f"{bob_temp_uri}/bob.txt", "bob", ctx=bob_ctx)

    alice_entries = await viking_fs.tree("viking://temp", output="original", ctx=alice_ctx)
    bob_entries = await viking_fs.tree("viking://temp", output="original", ctx=bob_ctx)

    alice_uris = {entry["uri"] for entry in alice_entries}
    bob_uris = {entry["uri"] for entry in bob_entries}

    assert any(uri.startswith(alice_temp_uri) for uri in alice_uris)
    assert not any(uri.startswith(bob_temp_uri) for uri in alice_uris)

    assert any(uri.startswith(bob_temp_uri) for uri in bob_uris)
    assert not any(uri.startswith(alice_temp_uri) for uri in bob_uris)


@pytest.mark.asyncio
async def test_temp_root_destructive_operations_are_blocked_for_non_root_users(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )

    with pytest.raises(PermissionError):
        await viking_fs.rm("viking://temp", recursive=True, ctx=alice_ctx)

    with pytest.raises(PermissionError):
        await viking_fs.delete_temp("viking://temp", ctx=alice_ctx)


@pytest.mark.asyncio
async def test_legacy_temp_trees_remain_accessible_for_same_account_users(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob", agent_id="agent2"),
        role=Role.USER,
    )

    legacy_temp_uri = "viking://temp/04011234_abcdef"
    legacy_secret_uri = f"{legacy_temp_uri}/legacy.txt"

    await viking_fs.mkdir(legacy_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.write(legacy_secret_uri, "legacy", ctx=alice_ctx)

    assert (await viking_fs.read(legacy_secret_uri, ctx=bob_ctx)).decode("utf-8") == "legacy"

    bob_entries = await viking_fs.tree("viking://temp", output="original", ctx=bob_ctx)
    bob_uris = {entry["uri"] for entry in bob_entries}

    assert legacy_temp_uri in bob_uris


def test_create_temp_uri_uses_user_scope_segment(viking_fs):
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )

    temp_uri = viking_fs.create_temp_uri(ctx=ctx)

    assert temp_uri.startswith(f"viking://temp/{ctx.user.user_space_name()}/")


def test_create_temp_uri_without_context_preserves_legacy_shape(viking_fs):
    temp_uri = viking_fs.create_temp_uri()

    assert temp_uri.startswith("viking://temp/")
    assert temp_uri.count("/") == 3


@pytest.mark.asyncio
async def test_legacy_temp_uri_remains_accessible_to_same_account_users(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob", agent_id="agent2"),
        role=Role.USER,
    )

    legacy_temp_uri = viking_fs.create_temp_uri()
    secret_uri = f"{legacy_temp_uri}/shared.txt"

    await viking_fs.mkdir(legacy_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.write(secret_uri, "legacy temp", ctx=alice_ctx)

    assert (await viking_fs.read(secret_uri, ctx=bob_ctx)).decode("utf-8") == "legacy temp"


@pytest.mark.asyncio
async def test_non_root_cannot_delete_temp_root_recursively(viking_fs):
    alice_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="alice", agent_id="agent1"),
        role=Role.USER,
    )
    bob_ctx = RequestContext(
        user=UserIdentifier(account_id="acct1", user_id="bob", agent_id="agent2"),
        role=Role.USER,
    )

    alice_temp_uri = viking_fs.create_temp_uri(ctx=alice_ctx)
    bob_temp_uri = viking_fs.create_temp_uri(ctx=bob_ctx)
    bob_secret_uri = f"{bob_temp_uri}/bob.txt"

    await viking_fs.mkdir(alice_temp_uri, exist_ok=True, ctx=alice_ctx)
    await viking_fs.mkdir(bob_temp_uri, exist_ok=True, ctx=bob_ctx)
    await viking_fs.write(bob_secret_uri, "bob secret", ctx=bob_ctx)

    with pytest.raises(PermissionError):
        await viking_fs.rm("viking://temp", recursive=True, ctx=alice_ctx)

    assert (await viking_fs.read(bob_secret_uri, ctx=bob_ctx)).decode("utf-8") == "bob secret"
