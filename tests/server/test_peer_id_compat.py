# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Peer ID request validation tests."""

import pytest

from openviking.server.routers.search import (
    FindRequest,
    SearchRequest,
)
from openviking.server.routers.sessions import AddMessageRequest


def test_search_request_preserves_legacy_agent_fields_without_peer_selector():
    request = SearchRequest.model_validate(
        {
            "query": "invoice",
            "agent_id": "code-agent",
            "agent_uri": "viking://agent/code-agent/skills",
        }
    )

    assert request.agent_id == "code-agent"
    assert request.agent_uri == "viking://agent/code-agent/skills"


def test_find_request_rejects_unpublished_peer_id_body_field():
    with pytest.raises(ValueError):
        FindRequest.model_validate({"query": "invoice", "peer_id": "code-agent"})


def test_add_message_request_does_not_map_agent_id_to_peer_id():
    request = AddMessageRequest.model_validate(
        {"role": "assistant", "content": "hello", "agent_id": "code-agent"}
    )

    assert request.peer_id is None


def test_add_message_request_normalizes_explicit_peer_id():
    request = AddMessageRequest.model_validate(
        {"role": "assistant", "content": "hello", "peer_id": " code-agent "}
    )

    assert request.peer_id == "code-agent"


def test_add_message_request_rejects_path_like_peer_id():
    with pytest.raises(ValueError, match="Invalid peer_id"):
        AddMessageRequest.model_validate(
            {
                "role": "assistant",
                "content": "hello",
                "peer_id": "code/agent",
            }
        )
