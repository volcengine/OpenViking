# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SemanticProcessor internal context reconstruction."""

from openviking.server.identity import Role
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def test_ctx_from_semantic_msg_preserves_identity_and_enables_acl_bypass(monkeypatch):
    monkeypatch.setattr(Role, "_CUSTOM_RANK", Role._CUSTOM_RANK.copy())
    Role.register("reviewer", 1)
    msg = SemanticMsg(
        uri="viking://resources/doc",
        context_type="resource",
        account_id="acme",
        user_id="alice",
        group_ids=["reviewers"],
        role="reviewer",
    )

    ctx = SemanticProcessor._ctx_from_semantic_msg(msg)

    assert ctx.account_id == "acme"
    assert ctx.user.user_id == "alice"
    assert ctx.group_ids == ("reviewers",)
    assert str(ctx.role) == "reviewer"
    assert ctx.bypass_acl is True
