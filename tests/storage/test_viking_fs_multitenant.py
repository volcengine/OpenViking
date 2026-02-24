# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Multi-tenant VikingFS path mapping and access control tests."""

import contextvars
from unittest.mock import MagicMock

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


def _make_ctx(role: Role = Role.USER) -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice", "helper"), role=role)


def _make_fs() -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = None
    fs.rerank_config = None
    fs.vector_store = None
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    return fs


def test_uri_to_path_user_scope_injects_user_space_for_user_role():
    fs = _make_fs()
    ctx = _make_ctx(Role.USER)

    actual = fs._uri_to_path("viking://user/memories/preferences/x.md", ctx=ctx)

    expected = f"/local/acme/user/{ctx.user.user_space_name()}/memories/preferences/x.md"
    assert actual == expected


def test_uri_to_path_session_scope_injects_user_space_when_missing():
    fs = _make_fs()
    ctx = _make_ctx(Role.USER)

    actual = fs._uri_to_path("viking://session/s1/messages.json", ctx=ctx)

    expected = f"/local/acme/session/{ctx.user.user_space_name()}/s1/messages.json"
    assert actual == expected


def test_path_to_uri_hides_own_space_for_user_role():
    fs = _make_fs()
    ctx = _make_ctx(Role.USER)
    user_space = ctx.user.user_space_name()

    actual = fs._path_to_uri(f"/local/acme/user/{user_space}/memories/a.md", ctx=ctx)

    assert actual == "viking://user/memories/a.md"


def test_is_accessible_for_user_enforces_user_and_agent_space_isolation():
    fs = _make_fs()
    user_ctx = _make_ctx(Role.USER)

    assert fs._is_accessible("viking://resources/doc.md", user_ctx)
    assert fs._is_accessible("viking://user/memories/preferences/me.md", user_ctx)
    assert fs._is_accessible("viking://agent/memories/cases/me.md", user_ctx)

    assert not fs._is_accessible("viking://user/other_space/memories/x.md", user_ctx)
    assert not fs._is_accessible("viking://agent/other_space/skills/s.md", user_ctx)


def test_is_accessible_root_and_admin_can_access_any_uri():
    fs = _make_fs()
    root_ctx = _make_ctx(Role.ROOT)
    admin_ctx = _make_ctx(Role.ADMIN)
    target = "viking://user/other_space/memories/x.md"

    assert fs._is_accessible(target, root_ctx)
    assert fs._is_accessible(target, admin_ctx)
