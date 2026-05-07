# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tenant-field backfill tests for EmbeddingMsgConverter."""

import pytest

from openviking.core.context import Context
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    ("uri", "expected_owner_user_id", "expected_owner_agent_id"),
    [
        (
            "viking://user/memories/preferences/me.md",
            lambda user: user.user_id,
            None,
        ),
        (
            "viking://agent/memories/cases/me.md",
            None,
            lambda user: user.agent_id,
        ),
        (
            "viking://resources/doc.md",
            None,
            None,
        ),
    ],
)
def test_embedding_msg_converter_backfills_account_and_owner_fields(
    uri, expected_owner_user_id, expected_owner_agent_id
):
    user = UserIdentifier("acme", "alice", "helper")
    context = Context(uri=uri, abstract="hello", user=user)

    # Simulate legacy producer that forgot tenant fields.
    context.account_id = ""
    context.owner_user_id = None
    context.owner_agent_id = None

    msg = EmbeddingMsgConverter.from_context(context)

    assert msg is not None
    assert msg.context_data["account_id"] == "acme"
    expected_user = (
        expected_owner_user_id(user) if callable(expected_owner_user_id) else expected_owner_user_id
    )
    expected_agent = (
        expected_owner_agent_id(user)
        if callable(expected_owner_agent_id)
        else expected_owner_agent_id
    )
    assert msg.context_data["owner_user_id"] == expected_user
    assert msg.context_data["owner_agent_id"] == expected_agent
