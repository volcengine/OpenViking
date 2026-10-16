# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.bot import _build_openviking_connection
from openviking_cli.session.user_id import UserIdentifier


def test_build_openviking_connection_forwards_actor_peer_id():
    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        actor_peer_id="peer-a",
    )
    connection = _build_openviking_connection(
        api_key="user-key",
        ctx=ctx,
        effective_auth_mode="api_key",
        server_url="http://127.0.0.1:1933",
    )
    # Assert the OpenViking-side forward only; vikingbot owns connection > body fallback.
    assert connection["actor_peer_id"] == "peer-a"


def test_build_openviking_connection_omits_blank_actor_peer_id():
    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        actor_peer_id="  ",
    )
    connection = _build_openviking_connection(
        api_key="user-key",
        ctx=ctx,
        effective_auth_mode="api_key",
        server_url="http://127.0.0.1:1933",
    )
    assert "actor_peer_id" not in connection
