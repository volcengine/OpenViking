# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.core.namespace import visible_roots
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import debug
from openviking.storage.expr import And, Or, PathScope, RawDSL
from openviking_cli.session.user_id import UserIdentifier


def _ctx(role: Role) -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=role)


def _visible_scope(ctx: RequestContext) -> Or:
    return Or([PathScope("uri", root, depth=-1) for root in visible_roots(ctx)])


def test_user_vector_filter_defaults_to_visible_roots():
    ctx = _ctx(Role.USER)

    assert debug._scope_vector_filter(None, ctx) == _visible_scope(ctx)


def test_user_vector_filter_cannot_be_replaced_by_raw_dsl():
    ctx = _ctx(Role.USER)
    raw_filter = {"op": "must", "field": "context_type", "conds": ["memory"]}

    assert debug._scope_vector_filter(raw_filter, ctx) == And(
        [_visible_scope(ctx), RawDSL(raw_filter)]
    )


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ROOT])
def test_privileged_vector_filter_behavior_is_unchanged(role: Role):
    ctx = _ctx(role)
    raw_filter = {"op": "must", "field": "context_type", "conds": ["memory"]}

    assert debug._scope_vector_filter(raw_filter, ctx) is raw_filter


class _FakeProxy:
    calls = []

    def __init__(self, _manager, ctx):
        self.ctx = ctx

    async def scroll(self, **kwargs):
        self.calls.append(("scroll", self.ctx, kwargs))
        return [], None

    async def count(self, **kwargs):
        self.calls.append(("count", self.ctx, kwargs))
        return 0


@pytest.fixture
def capture_proxy(monkeypatch):
    _FakeProxy.calls = []
    monkeypatch.setattr(
        debug,
        "get_service",
        lambda: SimpleNamespace(vikingdb_manager=object()),
    )
    monkeypatch.setattr(debug, "VikingDBManagerProxy", _FakeProxy)
    monkeypatch.setattr(debug, "resolve_path_variables", lambda uri: uri)
    monkeypatch.setattr(
        debug,
        "validate_request_viking_uri",
        lambda uri, _ctx: uri,
    )
    return _FakeProxy.calls


@pytest.mark.asyncio
async def test_user_scroll_intersects_explicit_uri_with_visible_roots(capture_proxy):
    ctx = _ctx(Role.USER)
    uri = "viking://user/bob/resources/private"

    await debug.debug_vector_scroll(limit=10, cursor=None, uri=uri, _ctx=ctx)

    assert capture_proxy == [
        (
            "scroll",
            ctx,
            {
                "filter": And(
                    [
                        _visible_scope(ctx),
                        RawDSL({"op": "must", "field": "uri", "conds": [uri]}),
                    ]
                ),
                "limit": 10,
                "cursor": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_user_count_keeps_raw_and_uri_filters_inside_visible_roots(capture_proxy):
    ctx = _ctx(Role.USER)
    uri = "viking://user/bob/resources/private"
    raw_filter = {"op": "must", "field": "context_type", "conds": ["memory"]}

    await debug.debug_vector_count(
        filter='{"op":"must","field":"context_type","conds":["memory"]}',
        uri=uri,
        _ctx=ctx,
    )

    assert capture_proxy == [
        (
            "count",
            ctx,
            {
                "filter": And(
                    [
                        _visible_scope(ctx),
                        And(
                            [
                                RawDSL(raw_filter),
                                RawDSL({"op": "must", "field": "uri", "conds": [uri]}),
                            ]
                        ),
                    ]
                )
            },
        )
    ]


@pytest.mark.asyncio
async def test_admin_scroll_without_uri_remains_account_wide(capture_proxy):
    ctx = _ctx(Role.ADMIN)

    await debug.debug_vector_scroll(limit=25, cursor="50", uri=None, _ctx=ctx)

    assert capture_proxy == [
        (
            "scroll",
            ctx,
            {"filter": None, "limit": 25, "cursor": "50"},
        )
    ]
