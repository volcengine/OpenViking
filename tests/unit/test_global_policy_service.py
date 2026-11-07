# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contracts for the Python lifecycle wrapper around UserGlobalPolicy."""

import logging

import pytest

from openviking.privacy import global_policy
from openviking.privacy.global_policy import UserGlobalPolicyService
from openviking.privacy.service import UserPrivacyConfigService
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
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


class _EncryptedFakeAGFS(_FakeAGFS):
    """Mirror an encrypted backend that refuses reads without ``account_id``."""

    def read(self, path, ctx=None):
        if not ctx or not ctx.get("account_id"):
            self.calls.append(path)
            self.contexts.append(ctx)
            raise RuntimeError("account_id missing in FsContext")
        return super().read(path, ctx=ctx)


class _FakeVikingFS:
    def __init__(self, contents=None):
        self.agfs = _FakeAGFS(contents or {})

    def _uri_to_path(self, uri, ctx=None):
        account = ctx.account_id if ctx is not None else "default"
        return f"/local/{account}/{uri[len('viking://') :]}"


def _ctx(account="acct", user="alice"):
    return RequestContext(user=UserIdentifier(account, user), role=Role.USER)


def _path(account="acct", user="alice"):
    return f"/local/{account}/user/{user}/GLOBAL_POLICY.md"


def test_service_reuses_one_native_policy_per_owner():
    fs = _FakeVikingFS({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    first = service.for_owner(ctx, "alice")
    second = service.for_owner(ctx, "alice")

    assert first is second
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is True
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is True
    assert fs.agfs.calls == [_path()]


def test_service_separates_accounts_for_the_same_user_id():
    fs = _FakeVikingFS(
        {
            _path("acct1"): _POLICY,
            _path("acct2"): _DENY_ALL,
        }
    )
    service = UserGlobalPolicyService(fs)

    first = service.for_owner(_ctx("acct1"), "alice")
    second = service.for_owner(_ctx("acct2"), "alice")

    assert first is not second
    assert first.allows_path("resources/public") is True
    assert second.allows_path("resources/public") is False
    assert len(fs.agfs.calls) == 2
    assert fs.agfs.contexts == [
        {"account_id": "acct1"},
        {"account_id": "acct2"},
    ]


def test_service_reads_policy_with_account_context_for_encrypted_backends():
    fs = _FakeVikingFS({_path(): _POLICY})
    fs.agfs = _EncryptedFakeAGFS({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    policy = service.for_owner(ctx, "alice")

    assert policy.status == "valid"
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is True
    assert fs.agfs.contexts == [{"account_id": "acct"}]


def test_invalidate_forces_the_next_access_to_reload():
    fs = _FakeVikingFS({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()
    policy = service.for_owner(ctx, "alice")

    assert service.allows(ctx, "resources/public", owner_user_id="alice") is True
    fs.agfs.contents[_path()] = _DENY_ALL

    service.invalidate(ctx, "alice")

    assert service.for_owner(ctx, "alice") is not policy
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is False
    assert len(fs.agfs.calls) == 2


def test_refresh_rebuilds_the_snapshot():
    fs = _FakeVikingFS({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()
    policy = service.for_owner(ctx, "alice")

    assert service.allows(ctx, "resources/public", owner_user_id="alice") is True
    fs.agfs.contents[_path()] = _DENY_ALL

    assert service.refresh(ctx, "alice") is True
    assert service.for_owner(ctx, "alice") is not policy
    assert service.allows(ctx, "resources/public", owner_user_id="alice") is False
    assert len(fs.agfs.calls) == 2


def test_ttl_expiry_rebuilds_the_snapshot(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(global_policy, "monotonic", lambda: clock["now"])
    fs = _FakeVikingFS({_path(): _POLICY})
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
    fs = _FakeVikingFS({_path(): RuntimeError("backend unavailable")})
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


def test_fifo_eviction_drops_the_oldest_entry_and_logs(monkeypatch, caplog):
    monkeypatch.setattr(global_policy, "POLICY_CACHE_MAX_ENTRIES", 2)
    fs = _FakeVikingFS(
        {
            _path(user="alice"): _POLICY,
            _path(user="bob"): _POLICY,
            _path(user="carol"): _POLICY,
        }
    )
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    service.for_owner(ctx, "alice")
    service.for_owner(ctx, "bob")
    global_policy.LOGGER.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="openviking.privacy.global_policy"):
            service.for_owner(ctx, "carol")
    finally:
        global_policy.LOGGER.removeHandler(caplog.handler)

    assert list(service._policies) == [("acct", "bob"), ("acct", "carol")]
    assert [record.getMessage() for record in caplog.records] == [
        "GLOBAL policy cache evicted the oldest entry: account=acct owner=alice size=2 max=2"
    ]

    service.for_owner(ctx, "alice")
    assert len(fs.agfs.calls) == 4


def test_fifo_eviction_does_not_promote_on_access(monkeypatch):
    monkeypatch.setattr(global_policy, "POLICY_CACHE_MAX_ENTRIES", 2)
    fs = _FakeVikingFS(
        {
            _path(user="alice"): _POLICY,
            _path(user="bob"): _POLICY,
            _path(user="carol"): _POLICY,
        }
    )
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    alice = service.for_owner(ctx, "alice")
    service.for_owner(ctx, "bob")
    assert service.for_owner(ctx, "alice") is alice

    service.for_owner(ctx, "carol")

    assert list(service._policies) == [("acct", "bob"), ("acct", "carol")]
    assert len(fs.agfs.calls) == 3

    service.for_owner(ctx, "alice")
    assert len(fs.agfs.calls) == 4


def test_allows_uri_derives_the_owner_and_relative_path():
    fs = _FakeVikingFS({_path(): _POLICY})
    service = UserGlobalPolicyService(fs)
    ctx = _ctx()

    assert service.allows_uri(ctx, "viking://user/alice/resources/public") is True
    assert service.allows_uri(ctx, "viking://user/alice/resources/private") is False
    assert service.allows_uri(ctx, "viking://user/alice/privacy/secret") is False
    assert service.allows_uri(ctx, "viking://user/bob/resources/public") is False
    assert service.allows_uri(ctx, "viking://resources/public") is False


def test_allows_uri_uses_the_policy_default_for_the_owner_root():
    allow_fs = _FakeVikingFS({_path(): _ALLOW_ALL})
    deny_fs = _FakeVikingFS({_path(): _DENY_ALL})
    allow_service = UserGlobalPolicyService(allow_fs)
    deny_service = UserGlobalPolicyService(deny_fs)
    ctx = _ctx()

    assert allow_service.allows_uri(ctx, "viking://user/alice") is True
    assert deny_service.allows_uri(ctx, "viking://user/alice") is False


def test_allows_uri_fails_closed_when_the_policy_is_missing():
    service = UserGlobalPolicyService(_FakeVikingFS())
    ctx = _ctx()

    assert service.allows_uri(ctx, "viking://user/alice") is False
    assert service.allows_uri(ctx, "viking://user/alice/resources/public") is False
    assert service.status(ctx, "alice") == "missing"


def test_invalidate_account_drops_only_that_account():
    fs = _FakeVikingFS(
        {
            _path("acct1"): _POLICY,
            _path("acct2"): _POLICY,
        }
    )
    service = UserGlobalPolicyService(fs)
    ctx1 = _ctx("acct1")
    ctx2 = _ctx("acct2")
    first = service.for_owner(ctx1, "alice")
    second = service.for_owner(ctx2, "alice")

    service.invalidate_account("acct1")

    assert service.for_owner(ctx1, "alice") is not first
    assert service.for_owner(ctx2, "alice") is second


def test_privacy_config_service_exposes_the_shared_registry():
    fs = _FakeVikingFS({_path(): _POLICY})
    privacy = UserPrivacyConfigService(fs)

    assert isinstance(privacy.global_policies, UserGlobalPolicyService)
    assert privacy.global_policies.for_owner(_ctx(), "alice") is privacy.global_policies.for_owner(
        _ctx(), "alice"
    )


def test_service_does_not_accept_a_ttl_override():
    with pytest.raises(TypeError):
        UserGlobalPolicyService(_FakeVikingFS(), ttl_seconds=1)
