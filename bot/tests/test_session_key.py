# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for filesystem-safe VikingBot session identifiers."""

import pytest
from vikingbot.config.schema import SessionKey


def test_safe_name_preserves_existing_safe_identifier():
    key = SessionKey(type="cli", channel_id="default", chat_id="session-1")

    assert key.safe_name() == "cli__default__session-1"
    assert SessionKey.from_safe_name(key.safe_name()) == key


@pytest.mark.parametrize("invalid_character", list('<>:"/\\|?*') + ["\x00", "\x1f"])
def test_safe_name_round_trips_windows_invalid_characters(invalid_character):
    key = SessionKey(
        type="cli",
        channel_id="default",
        chat_id=f"scope{invalid_character}session",
    )

    safe_name = key.safe_name()

    assert invalid_character not in safe_name
    assert SessionKey.from_safe_name(safe_name) == key


@pytest.mark.parametrize("suffix", [" ", "."])
def test_safe_name_encodes_windows_invalid_trailing_characters(suffix):
    key = SessionKey(type="cli", channel_id="default", chat_id=f"session{suffix}")

    safe_name = key.safe_name()

    assert not safe_name.endswith(suffix)
    assert SessionKey.from_safe_name(safe_name) == key


def test_safe_name_encoding_does_not_collide_with_underscore_replacement():
    colon_key = SessionKey(type="cli", channel_id="default", chat_id="scope:session")
    underscore_key = SessionKey(type="cli", channel_id="default", chat_id="scope_session")

    assert colon_key.safe_name() != underscore_key.safe_name()


def test_safe_name_round_trips_encoding_prefix():
    key = SessionKey(type="cli", channel_id="default", chat_id="~b32~legacy-looking")

    assert SessionKey.from_safe_name(key.safe_name()) == key
