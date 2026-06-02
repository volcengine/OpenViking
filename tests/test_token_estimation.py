# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Shared token estimation tests."""

from openviking.message import Message
from openviking.session import Session


def test_message_estimated_tokens_is_cjk_aware():
    """Chinese text should not be estimated with the ASCII chars/4 fallback."""
    msg = Message.create_user("\u4f60\u597d\u4e16\u754c")

    assert msg.estimated_tokens == 6


async def test_archive_overview_tokens_do_not_trust_stale_low_metadata():
    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del uri, ctx
            return '{"overview_tokens": 1}'

    fake_session = type("FakeSession", (), {"_viking_fs": FakeFS(), "ctx": None})()

    tokens = await Session._read_archive_overview_tokens(
        fake_session,
        "viking://session/test/history/archive_001",
        "\u4f60\u597d\u4e16\u754c",
    )

    assert tokens == 6


async def test_archive_overview_tokens_keep_higher_metadata_estimate():
    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del uri, ctx
            return '{"overview_tokens": 5}'

    fake_session = type("FakeSession", (), {"_viking_fs": FakeFS(), "ctx": None})()

    tokens = await Session._read_archive_overview_tokens(
        fake_session,
        "viking://session/test/history/archive_001",
        "abcd",
    )

    assert tokens == 5
