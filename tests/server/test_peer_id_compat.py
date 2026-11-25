# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Peer ID request validation tests."""

import pytest
from pydantic import ValidationError

from openviking.server.routers.search import FindRequest
from openviking.server.routers.sessions import AddMessageRequest


def test_find_request_rejects_unpublished_peer_id_body_field():
    with pytest.raises(ValidationError) as error:
        FindRequest.model_validate({"query": "invoice", "peer_id": "code-agent"})
    assert [(entry["loc"], entry["type"]) for entry in error.value.errors()] == [
        (("peer_id",), "extra_forbidden"),
    ]


def test_add_message_request_validates_explicit_peer_id():
    request = AddMessageRequest.model_validate(
        {"role": "assistant", "content": "hello", "peer_id": " code-agent "}
    )

    assert request.peer_id == "code-agent"

    with pytest.raises(ValidationError, match="Invalid peer_id") as error:
        AddMessageRequest.model_validate(
            {
                "role": "assistant",
                "content": "hello",
                "peer_id": "code/agent",
            }
        )
    assert error.value.errors()[0]["loc"] == ("peer_id",)
