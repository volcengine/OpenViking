# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Actor peer scope tests for VikingFS access rules."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    pass


@pytest.fixture
def fs() -> VikingFS:
    return VikingFS(agfs=_DummyAgfs())


def _ctx(actor_peer_id: str | None = None) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "support_bot"),
        role=Role.USER,
        actor_peer_id=actor_peer_id,
    )


def test_actor_scope_allows_resources_and_own_peer_subtree_only(fs: VikingFS):
    ctx = _ctx("alice")

    assert fs._is_accessible("viking://resources/runbooks/deploy.md", ctx) is True
    assert fs._is_accessible("viking://user/support_bot/peers", ctx) is True
    assert fs._is_accessible("viking://user/support_bot/peers/alice/memories", ctx) is True
    assert fs._is_accessible("viking://user/support_bot/peers/bob/memories", ctx) is False
    assert fs._is_accessible("viking://user/support_bot/memories", ctx) is False
    assert fs._is_accessible("viking://user/support_bot/skills", ctx) is False
    assert fs._is_accessible("viking://user/support_bot/privacy", ctx) is False


def test_actor_scope_can_mutate_only_own_peer_subtree(fs: VikingFS):
    ctx = _ctx("alice")

    fs._ensure_mutable_access(
        "viking://user/support_bot/peers/alice/memories/profile.md",
        ctx,
    )

    with pytest.raises(PermissionDeniedError):
        fs._ensure_mutable_access("viking://resources/runbooks/deploy.md", ctx)
    with pytest.raises(PermissionDeniedError):
        fs._ensure_mutable_access("viking://user/support_bot/peers/bob/memories/profile.md", ctx)
    with pytest.raises(PermissionDeniedError):
        fs._ensure_mutable_access("viking://user/support_bot/memories/profile.md", ctx)


async def test_actor_ls_user_peers_filters_to_actor_peer(fs: VikingFS, monkeypatch):
    async def fake_ls_entries(_path, ctx=None):
        return [
            {"name": "alice", "isDir": True},
            {"name": "bob", "isDir": True},
        ]

    monkeypatch.setattr(fs, "_ls_entries", fake_ls_entries)

    entries = await fs.ls("viking://user/peers", output="original", ctx=_ctx("alice"))

    assert [entry["name"] for entry in entries] == ["alice"]


def test_actor_tree_user_peers_filters_to_actor_peer(fs: VikingFS):
    ctx = _ctx("alice")
    base_path = "/local/acct/user/support_bot/peers"

    alice_entry = {"path": f"{base_path}/alice", "info": {"name": "alice"}}
    bob_entry = {"path": f"{base_path}/bob", "info": {"name": "bob"}}

    assert fs._is_tree_entry_visible(alice_entry, base_path, ctx) is True
    assert fs._is_tree_entry_visible(bob_entry, base_path, ctx) is False
