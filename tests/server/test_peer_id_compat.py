# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Peer ID compatibility tests."""

import pytest

from openviking.core.peer_id import (
    normalize_peer_selector,
)
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.search import (
    FindRequest,
    SearchRequest,
    _ctx_with_legacy_actor_peer,
)
from openviking.server.routers.sessions import AddMessageRequest
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


def test_normalize_peer_selector_accepts_legacy_agent_uri():
    assert normalize_peer_selector(None, agent_uri="viking://agent/code-agent/skills") == (
        "code-agent"
    )


def test_normalize_peer_selector_rejects_peer_id_with_legacy_agent_id():
    with pytest.raises(ValueError, match="peer_id cannot be used"):
        normalize_peer_selector("review-agent", agent_id="code-agent")


def test_normalize_peer_selector_rejects_mismatched_agent_id_and_uri():
    with pytest.raises(ValueError, match="legacy agent_id must match agent_uri"):
        normalize_peer_selector(
            None,
            agent_id="code-agent",
            agent_uri="viking://agent/review-agent/skills",
        )


def test_search_request_maps_legacy_agent_uri_to_agent_id():
    request = SearchRequest.model_validate(
        {"query": "invoice", "agent_uri": "viking://agent/code-agent/skills"}
    )

    assert request.agent_id == "code-agent"


def test_find_request_rejects_unpublished_peer_id_body_field():
    with pytest.raises(ValueError):
        FindRequest.model_validate({"query": "invoice", "peer_id": "code-agent"})


def test_add_message_request_maps_legacy_agent_id_to_peer_id():
    request = AddMessageRequest.model_validate(
        {"role": "assistant", "content": "hello", "agent_id": "code-agent"}
    )

    assert request.peer_id == "code-agent"


def test_add_message_request_rejects_peer_id_with_legacy_agent_id():
    with pytest.raises(ValueError, match="peer_id cannot be used"):
        AddMessageRequest.model_validate(
            {
                "role": "assistant",
                "content": "hello",
                "peer_id": "code-agent",
                "agent_id": "code-agent",
            }
        )


def test_legacy_search_peer_sets_actor_peer_context():
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)

    scoped = _ctx_with_legacy_actor_peer(ctx, "code-agent")

    assert scoped.actor_peer_id == "code-agent"
    assert scoped.legacy_agent_id == "code-agent"
    assert ctx.actor_peer_id is None


def test_legacy_search_peer_must_match_actor_peer_context():
    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        actor_peer_id="actor-a",
    )

    with pytest.raises(InvalidArgumentError, match="actor_peer_id cannot be used"):
        _ctx_with_legacy_actor_peer(ctx, "actor-b")
