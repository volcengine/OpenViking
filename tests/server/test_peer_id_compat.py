# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Peer ID request validation tests."""

import pytest

from openviking.server.routers.search import FindRequest
from openviking.server.routers.sessions import AddMessageRequest


def test_find_request_rejects_unpublished_peer_id_body_field():
    with pytest.raises(ValueError):
        FindRequest.model_validate({"query": "invoice", "peer_id": "code-agent"})


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
