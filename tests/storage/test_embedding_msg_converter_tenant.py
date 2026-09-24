# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Explicit account identity and URI owner tests for EmbeddingMsgConverter."""

import pytest

from openviking.core.context import Context, Vectorize
from openviking.storage.index_action import IndexAction
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.telemetry import OperationTelemetry, bind_telemetry
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    ("uri", "expected_uri", "expected_owner_user_id"),
    [
        (
            "viking://user/alice/memories/preferences/me.md",
            "viking://user/alice/memories/preferences/me.md",
            lambda user: user.user_id,
        ),
        (
            "viking://resources/doc.md",
            "viking://resources/doc.md",
            None,
        ),
    ],
)
def test_embedding_msg_converter_preserves_account_and_backfills_owner_fields(
    uri, expected_uri, expected_owner_user_id
):
    user = UserIdentifier("acme", "alice")
    context = Context(uri=uri, abstract="hello", user=user)

    # URI ownership may be derived, but account identity must remain explicit.
    context.owner_user_id = None

    msg = EmbeddingMsgConverter.from_context(context)

    assert msg is not None
    assert msg.context_data["account_id"] == "acme"
    resolved_uri = expected_uri(user) if callable(expected_uri) else expected_uri
    assert msg.context_data["uri"] == resolved_uri
    expected_user = (
        expected_owner_user_id(user) if callable(expected_owner_user_id) else expected_owner_user_id
    )
    assert msg.context_data["owner_user_id"] == expected_user
    assert msg.action is IndexAction.MERGE


def test_embedding_msg_converter_keeps_only_embedding_input():
    context = Context(uri="viking://resources/large.txt", abstract="short embedding text")
    context.set_vectorize(Vectorize(text="bounded embedding text"))

    msg = EmbeddingMsgConverter.from_context(context)

    assert msg is not None
    assert msg.message == "bounded embedding text"
    assert "content" not in msg.context_data
    assert msg.action is IndexAction.MERGE


def test_embedding_msg_converter_prefers_explicit_telemetry_id():
    context = Context(uri="viking://resources/doc.md", abstract="summary")
    ambient = OperationTelemetry(operation="ambient", enabled=False)

    with bind_telemetry(ambient):
        msg = EmbeddingMsgConverter.from_context(context, telemetry_id="request-telemetry-id")

    assert msg is not None
    assert msg.telemetry_id == "request-telemetry-id"
