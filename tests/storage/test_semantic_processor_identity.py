# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SemanticProcessor identity reconstruction."""

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.understanding_parse_msg import UnderstandingParseMsg


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


def test_ctx_from_semantic_msg_preserves_provider_request_context():
    msg = SemanticMsg(
        uri="viking://resources/doc",
        context_type="resource",
        account_id="acme",
        user_id="alice",
        provider_request_context={
            "headers": {
                "X-Provider-Service-JWT": "header.payload.signature",
                "X-Caller-Service-Code": "talentana",
            },
        },
    )

    ctx = SemanticProcessor._ctx_from_semantic_msg(msg)

    assert ctx.provider_request_context is not None
    assert ctx.provider_request_context.get_header("x-provider-service-jwt") == "header.payload.signature"
    assert ctx.provider_request_context.get_header("X-Caller-Service-Code") == "talentana"


def test_understanding_parse_msg_round_trips_provider_request_context():
    msg = UnderstandingParseMsg(
        task_id="task-1",
        path="https://example.com/doc.pdf",
        root_uri="viking://resources/doc",
        account_id="acme",
        user_id="alice",
        role="user",
        provider_request_context={
            "headers": {
                "X-Provider-Service-JWT": "header.payload.signature",
                "X-Caller-Service-Code": "talentana",
            },
        },
    )

    restored = UnderstandingParseMsg.from_dict(msg.to_dict())

    assert restored.provider_request_context == {
        "headers": {
            "X-Provider-Service-JWT": "header.payload.signature",
            "X-Caller-Service-Code": "talentana",
        },
    }
