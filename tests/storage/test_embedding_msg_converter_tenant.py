# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tenant-field backfill tests for EmbeddingMsgConverter."""

import pytest

from openviking.core.context import Context
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    ("uri", "expected_uri", "expected_owner_user_id"),
    [
        (
            "viking://user/memories/preferences/me.md",
            lambda user: f"viking://user/{user.user_id}/memories/preferences/me.md",
            lambda user: user.user_id,
        ),
        (
            "viking://resources/doc.md",
            "viking://resources/doc.md",
            None,
        ),
    ],
)
def test_embedding_msg_converter_backfills_account_and_owner_fields(
    uri, expected_uri, expected_owner_user_id
):
    user = UserIdentifier("acme", "alice")
    context = Context(uri=uri, abstract="hello", user=user)

    # Simulate legacy producer that forgot tenant fields.
    context.account_id = ""
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
