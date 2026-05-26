# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Peer ID compatibility tests."""

import pytest

from openviking.core.peer_id import normalize_peer_id
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.search import FindRequest, SearchRequest
from openviking.server.routers.sessions import AddMessageRequest
from openviking.service.search_service import _target_uri_for_peer
from openviking_cli.retrieve import ContextType
from openviking_cli.session.user_id import UserIdentifier


def test_normalize_peer_id_accepts_legacy_agent_id():
    assert normalize_peer_id(None, "web:visitor:alice") == "web:visitor:alice"
    assert normalize_peer_id("web:visitor:alice", "web:visitor:alice") == "web:visitor:alice"


def test_normalize_peer_id_rejects_conflicting_values():
    with pytest.raises(ValueError, match="peer_id and agent_id must match"):
        normalize_peer_id("web:visitor:alice", "web:visitor:bob")


def test_add_message_request_maps_agent_id_to_peer_id():
    request = AddMessageRequest(
        role="user",
        agent_id="web:visitor:alice",
        content="hello",
    )

    assert request.peer_id == "web:visitor:alice"


def test_search_requests_map_agent_id_to_peer_id():
    assert FindRequest(query="invoice", agent_id="web:visitor:alice").peer_id == (
        "web:visitor:alice"
    )
    assert SearchRequest(query="invoice", agent_id="web:visitor:alice").peer_id == (
        "web:visitor:alice"
    )


def test_peer_search_without_explicit_target_uses_self_and_peer_memory_roots():
    ctx = RequestContext(user=UserIdentifier("acct", "support_bot"), role=Role.USER)

    assert _target_uri_for_peer("", ctx, "web:visitor:alice") == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web:visitor:alice/memories",
    ]


def test_peer_search_keeps_explicit_target_uri():
    ctx = RequestContext(user=UserIdentifier("acct", "support_bot"), role=Role.USER)

    assert _target_uri_for_peer("viking://resources/docs", ctx, "web:visitor:alice") == (
        "viking://resources/docs"
    )


def test_default_memory_roots_include_all_peer_memories():
    ctx = RequestContext(user=UserIdentifier("acct", "support_bot"), role=Role.USER)
    retriever = HierarchicalRetriever(storage=None, embedder=None)

    assert retriever._get_root_uris_for_type(ContextType.MEMORY, ctx=ctx) == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers",
    ]
