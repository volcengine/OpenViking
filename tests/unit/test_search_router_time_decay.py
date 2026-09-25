# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for search time-decay request validation and routing."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openviking.retrieve.context_assembler import AssembleResult
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import search as search_router
from openviking_cli.session.user_id import UserIdentifier


def _request_context() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


def _http_request():
    return SimpleNamespace(state=SimpleNamespace())


async def test_search_router_forwards_time_decay_protection(monkeypatch):
    captured = {}

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(
        search_router,
        "get_service",
        lambda: SimpleNamespace(
            search=SimpleNamespace(search=fake_search),
            sessions=SimpleNamespace(),
        ),
    )

    response = await search_router.search(
        search_router.SearchRequest(query="sample", events_time_decay_protection="2d"),
        _http_request(),
        _request_context(),
    )

    assert response["status"] == "ok"
    assert captured["events_time_decay_protection"] == "2d"


@pytest.mark.parametrize("protection", ["1w", "-1d", "", "0.5", 0, True])
def test_search_request_rejects_invalid_protection(protection):
    with pytest.raises(ValidationError):
        search_router.SearchRequest(query="sample", events_time_decay_protection=protection)


@pytest.mark.parametrize("protection", [None, "0", "2d"])
def test_find_request_accepts_nullable_protection(protection):
    request = search_router.FindRequest(query="sample", events_time_decay_protection=protection)
    assert request.events_time_decay_protection == protection


def test_search_request_defaults_to_disabled():
    assert search_router.SearchRequest(query="sample").events_time_decay_protection is None


def test_filter_only_find_rejects_enabled_time_decay():
    with pytest.raises(ValidationError, match="semantic query or image"):
        search_router.FindRequest(
            filter={"op": "must", "field": "level", "conds": [2]},
            events_time_decay_protection="0",
        )


@pytest.mark.parametrize("protection", ["0", "2d"])
def test_context_mode_accepts_time_decay_parameter(protection):
    request = search_router.SearchRequest(
        query="sample", mode="context", events_time_decay_protection=protection
    )
    assert request.events_time_decay_protection == protection


def test_context_mode_accepts_explicit_null_decay_protection():
    request = search_router.SearchRequest(
        query="sample", mode="context", events_time_decay_protection=None
    )
    assert request.events_time_decay_protection is None


@pytest.mark.parametrize(
    "model, extra",
    [
        (search_router.FindRequest, {}),
        (search_router.SearchRequest, {"mode": "list"}),
        (search_router.SearchRequest, {"mode": "context"}),
    ],
)
@pytest.mark.parametrize("protection", [None, "0", "7d"])
def test_decay_preserves_negative_threshold_at_request_boundary(model, extra, protection):
    request = model(
        query="sample",
        score_threshold=-0.1,
        events_time_decay_protection=protection,
        **extra,
    )
    assert request.score_threshold == -0.1


async def test_context_router_forwards_time_decay_protection(monkeypatch):
    captured = {}

    async def fake_assemble_context(*, service, ctx, params):
        del service, ctx
        captured["params"] = params
        return AssembleResult()

    monkeypatch.setattr(search_router, "assemble_context", fake_assemble_context)
    response = await search_router._search_context(
        service=SimpleNamespace(),
        ctx=_request_context(),
        request=search_router.SearchRequest(
            query="sample",
            mode="context",
            events_time_decay_protection="2d",
        ),
        http_request=_http_request(),
        effective_filter=None,
        actual_limit=10,
    )

    assert response["status"] == "ok"
    assert captured["params"].events_time_decay_protection == "2d"
