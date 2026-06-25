# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SemanticProcessor identity reconstruction."""

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def test_ctx_from_semantic_msg_preserves_custom_role():
    msg = SemanticMsg(
        uri="viking://resources/doc",
        context_type="resource",
        account_id="acme",
        user_id="alice",
        role="reviewer",
    )

    ctx = SemanticProcessor._ctx_from_semantic_msg(msg)

    assert ctx.account_id == "acme"
    assert ctx.user.user_id == "alice"
    assert str(ctx.role) == "reviewer"


def test_ctx_from_semantic_msg_defaults_empty_role_to_root():
    msg = SemanticMsg(
        uri="viking://resources/doc",
        context_type="resource",
        role="",
    )

    ctx = SemanticProcessor._ctx_from_semantic_msg(msg)

    assert str(ctx.role) == "root"
