"""Regression tests for Telegram sender_id construction.

Covers the bug where ``_forward_command`` used only the numeric user ID,
causing ``is_allowed()`` to reject commands when ``allowFrom`` contained
usernames instead of numeric IDs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _make_channel(allow_from: list[str] | None = None):
    """Create a minimal TelegramChannel for unit testing."""
    from vikingbot.channels.telegram import TelegramChannel

    config = SimpleNamespace(
        type="telegram",
        enabled=True,
        token="fake-token",
        allow_from=allow_from or ["alice"],
        proxy=None,
        channel_id=lambda: "fake-token",
    )
    return TelegramChannel(config=config, bus=AsyncMock())


def _fake_user(user_id: int, username: str | None = None):
    return SimpleNamespace(id=user_id, username=username, first_name="Test")


# ---------------------------------------------------------------------------
# Unit: _build_sender_id
# ---------------------------------------------------------------------------


class TestBuildSenderId:

    def test_with_username(self):
        assert _make_channel()._build_sender_id(_fake_user(12345, "alice")) == "12345|alice"

    def test_without_username(self):
        assert _make_channel()._build_sender_id(_fake_user(12345, None)) == "12345"

    def test_empty_username(self):
        assert _make_channel()._build_sender_id(_fake_user(12345, "")) == "12345"


# ---------------------------------------------------------------------------
# Unit: is_allowed with constructed sender_id
# ---------------------------------------------------------------------------


class TestCommandAllowlist:

    def test_allowed_by_username(self):
        chan = _make_channel()
        sender_id = chan._build_sender_id(_fake_user(99999, "alice"))
        assert chan.is_allowed(sender_id) is True

    def test_denied_wrong_username(self):
        chan = _make_channel()
        sender_id = chan._build_sender_id(_fake_user(99999, "bob"))
        assert chan.is_allowed(sender_id) is False

    def test_denied_numeric_only_when_allowlist_has_username(self):
        """The exact regression: numeric-only sender_id cannot match username allowlist."""
        assert _make_channel().is_allowed("99999") is False

    def test_allowed_by_numeric_id_in_allowlist(self):
        chan = _make_channel(allow_from=["12345"])
        assert chan.is_allowed("12345") is True
        assert chan.is_allowed("12345|alice") is True


# ---------------------------------------------------------------------------
# Integration: _forward_command passes correct sender_id to _handle_message
# ---------------------------------------------------------------------------


class TestForwardCommandIntegration:

    @pytest.mark.asyncio
    async def test_forward_command_includes_username(self):
        chan = _make_channel()
        chan._handle_message = AsyncMock()

        update = SimpleNamespace(
            message=SimpleNamespace(chat_id=111, text="/new"),
            effective_user=_fake_user(99999, "alice"),
        )

        await chan._forward_command(update, context=None)

        chan._handle_message.assert_awaited_once_with(
            sender_id="99999|alice",
            chat_id="111",
            content="/new",
        )

    @pytest.mark.asyncio
    async def test_forward_command_numeric_only_when_no_username(self):
        chan = _make_channel()
        chan._handle_message = AsyncMock()

        update = SimpleNamespace(
            message=SimpleNamespace(chat_id=111, text="/help"),
            effective_user=_fake_user(99999, None),
        )

        await chan._forward_command(update, context=None)

        chan._handle_message.assert_awaited_once_with(
            sender_id="99999",
            chat_id="111",
            content="/help",
        )
