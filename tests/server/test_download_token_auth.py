# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Authentication for file-bound MCP download URLs."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from openviking.server import auth
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.auth.plugins.api_key import ApiKeyAuthPlugin
from openviking.server.auth.plugins.dev import DevAuthPlugin
from openviking.server.auth.plugins.trusted import TrustedAuthPlugin
from openviking.server.download_token_store import DownloadTokenStore
from openviking.server.identity import AuthMode, RequestContext, Role
from openviking_cli.exceptions import (
    FailedPreconditionError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import UserIdentifier


class _AccountManager:
    """In-memory account records stand in for the persisted account registry."""

    def __init__(self):
        self.roles = {("account-a", "alice"): Role(Role.ADMIN)}
        self.groups = {("account-a", "alice"): ("current-group",)}
        self.deletions = {}

    async def get_registered_user_role(self, account_id, user_id):
        return self.roles.get((account_id, user_id))

    async def get_user_group_ids(self, account_id, user_id):
        return self.groups.get((account_id, user_id), ())

    async def get_deletion(self, account_id, user_id):
        return self.deletions.get((account_id, user_id))


@pytest.fixture
def store(monkeypatch):
    store = DownloadTokenStore()
    monkeypatch.setattr(auth, "download_token_store", store)
    return store


def _issue(store, role=Role.ADMIN, from_oauth=True):
    ctx = RequestContext(
        user=UserIdentifier("account-a", "alice"),
        role=role,
        actor_peer_id="original-peer",
        group_ids=("old-group",),
        from_oauth=from_oauth,
        api_key="original-secret",
        bypass_acl=True,
    )
    return store.issue("viking://resources/report.pdf", ctx)[0]


def _request(mode=AuthMode.API_KEY):
    plugin: AuthPlugin
    if mode == AuthMode.API_KEY:
        plugin = ApiKeyAuthPlugin()
    elif mode == AuthMode.TRUSTED:
        plugin = TrustedAuthPlugin()
    else:
        plugin = DevAuthPlugin()
    manager = _AccountManager()
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=SimpleNamespace(get_effective_auth_mode=lambda: mode),
            auth_plugin=plugin,
            api_key_manager=manager,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("localhost", 1933),
            "path": "/api/v1/content/download/token",
            "query_string": b"uri=viking://resources/other-file",
            "headers": [
                (b"x-api-key", b"spoofed-key"),
                (b"authorization", b"Bearer spoofed-token"),
                (b"x-openviking-account", b"other-account"),
                (b"x-openviking-user", b"mallory"),
                (b"x-openviking-role", b"root"),
                (b"x-openviking-actor-peer", b"other-peer"),
            ],
            "app": app,
        }
    )


async def test_download_uses_bound_identity_and_current_groups_ignoring_headers(store):
    request = _request()
    token = _issue(store)

    ctx = await auth.get_download_request_context(request, token)

    assert ctx.account_id == "account-a"
    assert ctx.user.user_id == "alice"
    assert ctx.role == Role.ADMIN
    assert ctx.actor_peer_id == "original-peer"
    assert ctx.from_oauth is True
    assert ctx.group_ids == ("current-group",)
    assert ctx.api_key is None
    assert ctx.bypass_acl is False
    assert request.state.signed_download.uri == "viking://resources/report.pdf"
    with pytest.raises(HTTPException) as replay:
        await auth.get_download_request_context(request, token)
    assert replay.value.status_code == 401


@pytest.mark.parametrize("current_role", [None, Role.USER])
async def test_download_rejects_deleted_or_demoted_user(store, current_role):
    request = _request()
    request.app.state.api_key_manager.roles[("account-a", "alice")] = (
        Role(current_role) if current_role is not None else None
    )

    with pytest.raises(UnauthenticatedError):
        await auth.get_download_request_context(request, _issue(store))


async def test_download_does_not_inherit_a_later_role_promotion(store):
    ctx = await auth.get_download_request_context(_request(), _issue(store, role=Role.USER))
    assert ctx.role == Role.USER


@pytest.mark.parametrize("mode", [AuthMode.API_KEY, AuthMode.TRUSTED])
async def test_download_rejects_identity_deletion_in_progress(store, mode):
    request = _request(mode)
    request.app.state.api_key_manager.deletions[("account-a", "alice")] = {
        "task_id": "delete-alice"
    }

    with pytest.raises(FailedPreconditionError, match="deletion is in progress"):
        await auth.get_download_request_context(request, _issue(store))


async def test_trusted_download_preserves_bound_role_and_current_groups(store):
    ctx = await auth.get_download_request_context(
        _request(AuthMode.TRUSTED), _issue(store, from_oauth=False)
    )
    assert ctx.role == Role.ADMIN
    assert ctx.group_ids == ("current-group",)
    assert ctx.from_oauth is False


async def test_trusted_short_lived_file_grant_retains_issued_role_after_local_demotion(store):
    request = _request(AuthMode.TRUSTED)
    token = _issue(store, from_oauth=False)
    request.app.state.api_key_manager.roles[("account-a", "alice")] = Role(Role.USER)

    ctx = await auth.get_download_request_context(request, token)

    assert ctx.role == Role.ADMIN
    assert ctx.group_ids == ("current-group",)
    assert request.state.signed_download.uri == "viking://resources/report.pdf"
    with pytest.raises(HTTPException) as replay:
        await auth.get_download_request_context(request, token)
    assert replay.value.status_code == 401


async def test_dev_download_preserves_default_root_context(store):
    ctx = await auth.get_download_request_context(
        _request(AuthMode.DEV), _issue(store, role=Role.ROOT, from_oauth=False)
    )
    assert ctx.role == Role.ROOT
    assert ctx.actor_peer_id == "original-peer"
    assert ctx.api_key is None
    assert ctx.bypass_acl is False


async def test_unknown_download_token_returns_unauthorized(store):
    with pytest.raises(HTTPException) as failure:
        await auth.get_download_request_context(_request(), "unknown-token")
    assert failure.value.status_code == 401


async def test_download_does_not_assume_custom_auth_user_privileges(store):
    request = _request(AuthMode.DEV)
    request.app.state.config.get_effective_auth_mode = lambda: "external"
    request.app.state.auth_plugin.auth_mode = "external"

    ctx = await auth.get_download_request_context(request, _issue(store, role=Role("guest")))
    assert ctx.role == "guest"


async def test_download_enforces_custom_plugin_context_checks(store):
    class DownloadDeniedPlugin(DevAuthPlugin):
        auth_mode = "external"

        def get_request_context_checks(self, path, identity):
            raise PermissionDeniedError("Custom plugin forbids token downloads")

    request = _request(AuthMode.DEV)
    request.app.state.config.get_effective_auth_mode = lambda: "external"
    request.app.state.auth_plugin = DownloadDeniedPlugin()

    with pytest.raises(PermissionDeniedError, match="Custom plugin forbids"):
        await auth.get_download_request_context(request, _issue(store, role=Role("guest")))
