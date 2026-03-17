# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tenant-field backfill tests for EmbeddingMsgConverter."""

import pytest

from openviking.core.context import Context, ModalContent, Vectorize
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    ("uri", "expected_space"),
    [
        (
            "viking://user/memories/preferences/me.md",
            lambda user: user.user_space_name(),
        ),
        (
            "viking://agent/memories/cases/me.md",
            lambda user: user.agent_space_name(),
        ),
        (
            "viking://resources/doc.md",
            lambda _user: "",
        ),
    ],
)
def test_embedding_msg_converter_backfills_account_and_owner_space(uri, expected_space):
    user = UserIdentifier("acme", "alice", "helper")
    context = Context(uri=uri, abstract="hello", user=user)

    # Simulate legacy producer that forgot tenant fields.
    context.account_id = ""
    context.owner_space = ""

    msg = EmbeddingMsgConverter.from_context(context)

    assert msg is not None
    assert msg.context_data["account_id"] == "acme"
    assert msg.context_data["owner_space"] == expected_space(user)


def test_embedding_msg_media_fields_default_none():
    msg = EmbeddingMsg(message="hello", context_data={"uri": "viking://x"})
    assert msg.media_uri is None
    assert msg.media_mime_type is None


def test_embedding_msg_media_round_trip():
    """media_uri and media_mime_type survive to_dict/from_dict serialization."""
    msg = EmbeddingMsg(
        message="a photo",
        context_data={"uri": "viking://agent/resources/img.jpg"},
        media_uri="viking://agent/resources/img.jpg",
        media_mime_type="image/jpeg",
    )
    restored = EmbeddingMsg.from_dict(msg.to_dict())
    assert restored.media_uri == "viking://agent/resources/img.jpg"
    assert restored.media_mime_type == "image/jpeg"


def test_embedding_msg_legacy_message_missing_media_fields():
    """Old queue messages without media fields deserialize cleanly (None defaults)."""
    old_payload = {"message": "old text", "context_data": {"uri": "viking://x"}, "id": "abc"}
    msg = EmbeddingMsg.from_dict(old_payload)
    assert msg.media_uri is None
    assert msg.media_mime_type is None


def test_converter_passes_media_uri_when_vectorize_has_media():
    context = Context(uri="viking://agent/resources/shot.png", abstract="screenshot")
    mc = ModalContent(mime_type="image/png", uri="viking://agent/resources/shot.png")
    context.set_vectorize(Vectorize(text="screenshot of dashboard", media=mc))

    msg = EmbeddingMsgConverter.from_context(context)

    assert msg.media_uri == "viking://agent/resources/shot.png"
    assert msg.media_mime_type == "image/png"


def test_converter_media_fields_none_for_text_only():
    context = Context(uri="viking://agent/memories/notes.md", abstract="some note")
    context.set_vectorize(Vectorize(text="some note text"))

    msg = EmbeddingMsgConverter.from_context(context)

    assert msg.media_uri is None
    assert msg.media_mime_type is None


def test_converter_allows_media_even_with_empty_text():
    """If media is present but text is empty, use URI as fallback — don't drop the message."""
    context = Context(uri="viking://agent/resources/img.png", abstract="")
    mc = ModalContent(mime_type="image/png", uri="viking://agent/resources/img.png")
    context.set_vectorize(Vectorize(text="", media=mc))

    msg = EmbeddingMsgConverter.from_context(context)

    # Should NOT be None — media-only messages must pass through
    assert msg is not None
    assert msg.media_uri == "viking://agent/resources/img.png"
    # Text should be the URI as fallback (or some non-empty string)
    assert msg.message  # non-empty
