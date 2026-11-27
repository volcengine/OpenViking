# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contracts for the Python lifecycle wrapper around UserGlobalPolicy."""

import pytest

from openviking.privacy import global_policy
from openviking.privacy.global_policy import UserGlobalPolicyService
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

_POLICY = """---
type: global-access-policy
schema_version: 1
default: deny
rules:
  - effect: allow
    path: resources/public
    recursive: true
---
"""

_DENY_ALL = """---
type: global-access-policy
schema_version: 1
default: deny
rules: []
---
"""

_ALLOW_ALL = """---
type: global-access-policy
schema_version: 1
default: allow
rules: []
---
"""


class _FakeAGFS:
    def __init__(self, contents):
        self.contents = contents
        self.calls = []
        self.contexts = []

    def read(self, path, ctx=None):
        self.calls.append(path)
        self.contexts.append(ctx)
        value = self.contents.get(path)
        if value is None:
            raise AGFSNotFoundError(path)
        if isinstance(value, Exception):
            raise value
        return value


def _viking_fs(contents=None):
    return VikingFS(agfs=_FakeAGFS(contents or {}))


def _ctx(account="acct", user="alice"):
    return RequestContext(user=UserIdentifier(account, user), role=Role.USER)


def _path(account="acct", user="alice"):
    return f"/local/{account}/user/{user}/GLOBAL_POLICY.md"


def test_service_separates_accounts_for_the_same_user_id():
    fs = _viking_fs({_path("acct1"): _POLICY, _path("acct2"): _DENY_ALL})
    service = UserGlobalPolicyService(fs)
    fs.set_global_policy_service(service)
    uri = "viking://user/alice/resources/public/doc.md"
    role = Role.parse(Role.GLOBAL)
    actor1 = RequestContext(user=UserIdentifier("acct1", "reader"), role=role)
    actor2 = RequestContext(user=UserIdentifier("acct2", "reader"), role=role)

    authorized = fs.authorize_read(actor1, [uri])
    assert authorized.data_access.owner_user_id == "alice"
    assert authorized.data_access.effective_targets == (uri,)
    with pytest.raises(PermissionDeniedError, match="no allowed content"):
        fs.authorize_read(actor2, [uri])
    assert fs.authorize_read(actor1, [uri]).data_access == authorized.data_access
    assert fs.agfs.calls == [_path("acct1"), _path("acct2")]
    assert fs.agfs.contexts == [{"account_id": "acct1"}, {"account_id": "acct2"}]

    fs.agfs.contents[_path("acct1")] = _DENY_ALL
    fs.agfs.contents[_path("acct2")] = _POLICY
    service.invalidate_account("acct1")
    for actor in (actor1, actor2):
        with pytest.raises(PermissionDeniedError, match="no allowed content"):
            fs.authorize_read(actor, [uri])
    assert fs.agfs.calls == [_path("acct1"), _path("acct2"), _path("acct1")]


def test_invalidate_forces_the_next_access_to_reload():
    fs = _viking_fs({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()
    uri = "viking://user/alice/resources/public/file.md"
    assert service.allows_uri(ctx, uri)

    fs.agfs.contents[_path()] = _DENY_ALL
    service.invalidate(ctx, "alice")
    assert not service.allows_uri(ctx, uri)

    fs.agfs.contents[_path()] = _POLICY
    assert service.refresh(ctx, "alice")
    assert service.allows_uri(ctx, uri)
    assert fs.agfs.calls == [_path()] * 3


def test_ttl_expiry_rebuilds_the_snapshot(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(global_policy, "monotonic", lambda: clock["now"])
    fs = _viking_fs({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    first = service.for_owner(ctx, "alice")
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is True

    clock["now"] = global_policy.POLICY_CACHE_TTL_SECONDS
    fs.agfs.contents[_path()] = _DENY_ALL

    second = service.for_owner(ctx, "alice")
    assert second is not first
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is False
    assert len(fs.agfs.calls) == 2


def test_unreadable_policy_is_not_cached_and_retries():
    fs = _viking_fs({_path(): RuntimeError("backend unavailable")})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    first = service.for_owner(ctx, "alice")
    assert first.status == "unreadable"
    assert len(fs.agfs.calls) == 1

    second = service.for_owner(ctx, "alice")
    assert second is not first
    assert len(fs.agfs.calls) == 2

    fs.agfs.contents[_path()] = _POLICY
    third = service.for_owner(ctx, "alice")
    assert third.status == "valid"
    assert len(fs.agfs.calls) == 3


def test_fifo_eviction_does_not_promote_on_access(monkeypatch):
    monkeypatch.setattr(global_policy, "POLICY_CACHE_MAX_ENTRIES", 2)
    fs = _viking_fs({_path(user=user): _POLICY for user in ("alice", "bob", "carol")})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()
    assert service.allows(ctx, "resources/public", owner_user_id="alice")
    assert service.allows(ctx, "resources/public", owner_user_id="bob")
    assert service.allows(ctx, "resources/public", owner_user_id="alice")
    assert service.allows(ctx, "resources/public", owner_user_id="carol")
    assert service.allows(ctx, "resources/public", owner_user_id="bob")

    fs.agfs.contents[_path(user="alice")] = _DENY_ALL
    assert not service.allows(ctx, "resources/public", owner_user_id="alice")
    assert fs.agfs.calls == [_path(user=user) for user in ("alice", "bob", "carol", "alice")]


def test_allows_uri_derives_the_owner_and_relative_path():
    fs = _viking_fs({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    assert service.allows_uri(ctx, "viking://user/alice/resources/public") is True
    assert service.allows_uri(ctx, "viking://user/alice/resources/private") is False
    assert service.allows_uri(ctx, "viking://user/alice/privacy/secret") is False
    assert service.allows_uri(ctx, "viking://user/bob/resources/public") is False
    assert service.allows_uri(ctx, "viking://resources/public") is False


def test_allows_uri_uses_the_policy_default_for_the_owner_root():
    allow_fs = _viking_fs({_path(): _ALLOW_ALL})
    deny_fs = _viking_fs({_path(): _DENY_ALL})
    allow_service = UserGlobalPolicyService(allow_fs)
    deny_service = UserGlobalPolicyService(deny_fs)
    ctx = _ctx()

    assert allow_service.allows_uri(ctx, "viking://user/alice") is True
    assert deny_service.allows_uri(ctx, "viking://user/alice") is False


def test_allows_uri_fails_closed_when_the_policy_is_missing():
    service = UserGlobalPolicyService(_viking_fs())
    ctx = _ctx()

    assert service.allows_uri(ctx, "viking://user/alice") is False
    assert service.allows_uri(ctx, "viking://user/alice/resources/public") is False
    assert service.status(ctx, "alice") == "missing"
