# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from openviking.server.account_stores.base import AccountStore
from openviking.server.api_keys import APIKeyManager
from openviking.server.auth import (
    get_request_context,
    get_session_request_context,
)
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.auth.plugins.api_key import ApiKeyAuthPlugin
from openviking.server.auth.plugins.trusted import TrustedAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    UnauthenticatedError,
)
from tests.server.account_store_fakes import InMemoryAGFS


@pytest.fixture
async def runtime():
    manager = build_api_key_manager(
        "root",
        SimpleNamespace(agfs=InMemoryAGFS()),
    )
    await manager.load()
    await manager.create_account("acme", "alice")
    yield manager
    await manager.close()


async def make_request(
    runtime,
    mode="api_key",
    root="root",
    headers=(),
    path="/api/v1/content/read",
    trusted_flush_interval=0,
):
    config = ServerConfig(
        auth_mode=mode,
        root_api_key=root,
        trusted_identity_flush_interval_seconds=trusted_flush_interval,
    )
    plugin = TrustedAuthPlugin() if mode == "trusted" else ApiKeyAuthPlugin()
    state = SimpleNamespace(config=config, api_key_manager=runtime, auth_plugin=plugin)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": list(headers),
            "query_string": b"",
            "app": SimpleNamespace(state=state),
        }
    )
    if mode == "trusted":
        plugin._api_key_manager = runtime
        plugin._flush_interval_seconds = trusted_flush_interval
        if root is None:
            request.app.state.api_key_manager = None
    return request


async def test_key_identity_and_groups_are_resolved_independently(runtime):
    key = await runtime.register_user("acme", "bob")
    await runtime.create_group("acme", "team")
    await runtime.add_group_member("acme", "team", "bob")
    request = await make_request(runtime, headers=[(b"x-openviking-user", b"spoof")])
    plugin = request.app.state.auth_plugin
    identity = await plugin.resolve_identity(
        request, api_key=key, x_openviking_user="spoof", x_openviking_account="spoof"
    )
    ctx = await get_request_context(request, identity, "peer", key, None)
    assert (ctx.user.user_id, ctx.role, ctx.group_ids) == ("bob", Role.USER, ("team",))
    assert ctx.actor_peer_id == "peer"
    assert ctx.api_key == key
    assert "x-openviking-user" not in request.headers
    session = await get_session_request_context(request, identity, key, None)
    assert session.actor_peer_id is None


async def test_key_store_error_and_unknown_identity_fail_closed():
    store = AsyncMock(spec=AccountStore)
    store.verify_api_key.return_value = ("acme", "missing")
    store.get_user.return_value = None
    manager = APIKeyManager("root", store)
    request = await make_request(manager)
    with pytest.raises(UnauthenticatedError, match="Unknown"):
        await request.app.state.auth_plugin.resolve_identity(request, api_key="verified-key")
    store.get_user.side_effect = OSError("database offline")
    with pytest.raises(OSError, match="database offline"):
        await request.app.state.auth_plugin.resolve_identity(request, api_key="verified-key")


async def test_trusted_explicit_user_role_with_root_key(runtime):
    request = await make_request(runtime, "trusted", "root")
    plugin = request.app.state.auth_plugin
    identity = await plugin.resolve_identity(
        request, api_key="root", x_openviking_account="acme", x_openviking_user="alice"
    )
    assert identity.role == Role.ADMIN
    asserted = await make_request(
        runtime, "trusted", "root", headers=[(b"x-openviking-role", b"user")]
    )
    identity = await plugin.resolve_identity(
        asserted,
        api_key="root",
        x_openviking_account="acme",
        x_openviking_user="alice",
    )
    assert identity.role == Role.USER


async def test_oversized_trusted_identity_does_not_block_valid_registration(runtime):
    request = await make_request(runtime, "trusted", trusted_flush_interval=300)
    plugin = request.app.state.auth_plugin
    account_id = "a" * 255
    await plugin.resolve_identity(
        request, api_key="root", x_openviking_account=account_id, x_openviking_user="alice"
    )

    with pytest.raises(InvalidArgumentError, match="account_id.*255.*UTF-8 bytes"):
        await plugin.resolve_identity(
            request,
            api_key="root",
            x_openviking_account="a" * 256,
            x_openviking_user="bob",
        )

    long_user_id = "u" * 256
    with pytest.raises(InvalidArgumentError, match="user_id.*255"):
        await plugin.resolve_identity(
            request, api_key="root", x_openviking_account="acme", x_openviking_user=long_user_id
        )
    await plugin.flush_trusted_identities()

    assert await runtime.get_registered_user_role(account_id, "alice") == Role.USER
    assert await runtime.get_registered_user_role("acme", long_user_id) is None


async def test_trusted_registration_preserves_existing_oversized_account():
    account_id = "a" * 1024
    agfs = InMemoryAGFS()
    agfs.write(
        "/local/_system/accounts.json",
        json.dumps({"accounts": {account_id: {"created_at": "legacy"}}}).encode(),
    )
    agfs.write(
        f"/local/{account_id}/_system/users.json",
        json.dumps({"users": {"alice": {"role": "admin"}}}).encode(),
    )
    manager = build_api_key_manager("root", SimpleNamespace(agfs=agfs), issue_user_keys=False)
    await manager.load()
    try:
        request = await make_request(manager, "trusted", trusted_flush_interval=300)
        plugin = request.app.state.auth_plugin
        identity = await plugin.resolve_identity(
            request, api_key="root", x_openviking_account=account_id, x_openviking_user="alice"
        )
        assert identity.role == Role.ADMIN

        await plugin.resolve_identity(
            request, api_key="root", x_openviking_account=account_id, x_openviking_user="bob"
        )
        await plugin.flush_trusted_identities()

        assert await manager.get_registered_user_role(account_id, "bob") == Role.USER
    finally:
        await manager.close()


async def test_trusted_unknown_user_cannot_bypass_account_deletion(runtime):
    """[defect-probing] Account fence also covers not-yet-registered trusted users."""
    await runtime.begin_deletion(
        "acme", None, task_id="delete", owner_account_id="system", owner_user_id="system"
    )
    request = await make_request(runtime, "trusted")
    identity = await request.app.state.auth_plugin.resolve_identity(
        request, api_key="root", x_openviking_account="acme", x_openviking_user="newcomer"
    )
    with pytest.raises(FailedPreconditionError) as exc:
        await get_request_context(request, identity, None, "root", None)
    assert exc.value.details["task_id"] == "delete"


async def test_root_context_remains_available_while_default_account_is_deleting(runtime):
    if await runtime.get_registered_user_role("default", "default") is None:
        await runtime.register_user("default", "default", Role.ADMIN)
    await runtime.begin_deletion(
        "default",
        None,
        task_id="delete-default",
        owner_account_id="system",
        owner_user_id="system",
    )
    request = await make_request(runtime, path="/api/v1/admin/accounts")
    identity = await request.app.state.auth_plugin.resolve_identity(request, api_key="root")

    ctx = await get_request_context(request, identity, None, "root", None)

    assert (ctx.role, ctx.account_id, ctx.user.user_id, ctx.group_ids) == (
        Role.ROOT,
        "default",
        "default",
        (),
    )


async def test_external_context_preserves_role_without_local_gate(runtime):
    class ExternalPlugin(AuthPlugin):
        auth_mode = "external"

        async def initialize(self, app, service, config):
            pass

        def validate_config(self, config):
            pass

        async def resolve_identity(self, request, **kwargs):
            return ResolvedIdentity(role=Role.USER, account_id="external", user_id="subject")

    request = await make_request(runtime, "external")
    plugin = ExternalPlugin()
    request.app.state.auth_plugin = plugin
    identity = await plugin.resolve_identity(request)
    ctx = await get_request_context(request, identity, None, None, None)
    assert (ctx.role, ctx.account_id, ctx.group_ids) == (Role.USER, "external", ())


async def test_trusted_cancelled_flush_requeues_batch_and_shutdown_preserves_store(
    runtime, monkeypatch
):
    request = await make_request(runtime, "trusted", trusted_flush_interval=300)
    plugin = request.app.state.auth_plugin
    await plugin.resolve_identity(
        request,
        api_key="root",
        x_openviking_account="acme",
        x_openviking_user="bob",
    )
    entered = asyncio.Event()
    original = runtime.ensure_trusted_identities

    async def blocked_batch(identities):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(runtime, "ensure_trusted_identities", blocked_batch)
    flush = asyncio.create_task(plugin.flush_trusted_identities())
    await entered.wait()
    flush.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flush
    monkeypatch.setattr(runtime, "ensure_trusted_identities", original)
    await plugin.shutdown()
    assert await runtime.has_user("acme", "bob")
    assert await runtime.get_registered_user_role("acme", "alice") == Role.ADMIN


@pytest.mark.parametrize(
    "mode,custom", [("oidc", False), ("oidc", True), ("ldap", False), ("ldap", True)]
)
async def test_builtin_external_mapping_preserves_identity_and_user_role(
    runtime, mode, custom, monkeypatch
):
    from openviking.server.auth.identity_mapping import IdentityMapper
    from openviking.server.auth.ldap_config import LDAPConfig
    from openviking.server.auth.oidc_config import OIDCConfig
    from openviking.server.auth.plugins import ldap as ldap_module
    from openviking.server.auth.plugins import oidc as oidc_module

    if mode == "oidc":
        plugin = oidc_module.OIDCAuthPlugin()
        config = OIDCConfig(issuer="https://issuer.test")
        headers = [(b"authorization", b"Bearer external.token.value")]
        monkeypatch.setattr(oidc_module, "_check_jwt_available", lambda: True)
        monkeypatch.setattr(
            plugin,
            "_validate_token",
            AsyncMock(return_value={"sub": "auth0|123", "org": "Team", "preferred": "Alice"}),
        )
        expected = ("default", "auth0_123")
        if custom:
            config.identity.account_id.source = "claim"
            config.identity.account_id.claim = "org"
            config.identity.account_id.normalize = "lowercase"
            config.identity.user_id.claims = ["preferred", "sub"]
            config.identity.user_id.prefix = "ext-"
            expected = ("team", "ext-Alice")
    else:
        plugin = ldap_module.LDAPAuthPlugin()
        config = LDAPConfig(host="directory.test", base_dn="dc=test")
        headers = [(b"authorization", b"Basic YWxpY2U6cGFzc3dvcmQ=")]
        monkeypatch.setattr(ldap_module, "LDAP_AVAILABLE", True)
        monkeypatch.setattr(
            plugin,
            "_authenticate_ldap",
            lambda *args: {
                "uid": [b"directory-user"],
                "dn": [b"cn=alice,ou=Team,dc=test"],
            },
        )
        expected = ("default", "directory-user")
        if custom:
            config.identity.user_id.source = "dn_attribute"
            config.identity.user_id.attribute = "cn"
            config.identity.account_id.source = "dn_attribute"
            config.identity.account_id.attribute = "ou"
            expected = ("Team", "alice")
    config.identity.role.source = "fixed"
    config.identity.role.value = "root"
    plugin._config = config
    plugin._mapper = IdentityMapper(config.identity)
    request = await make_request(runtime, mode, headers=headers)
    request.app.state.auth_plugin = plugin
    identity = await plugin.resolve_identity(request)
    assert (identity.account_id, identity.user_id) == expected
    assert identity.role == Role.USER
    ctx = await get_request_context(request, identity, None, None, None)
    assert ctx.role == Role.USER
