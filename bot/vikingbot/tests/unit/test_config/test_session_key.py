"""Regression tests for SessionKey.from_safe_name separator handling.

Bug #9: ``from_safe_name`` used ``safe_name.split("__")`` without
``maxsplit``.  If a ``chat_id`` field contains the separator (e.g. a
feishu topic id, a Discord thread id like ``thread__42``), the round
trip through ``safe_name`` → ``from_safe_name`` would silently truncate
the chat_id to the part before the first inner ``__``, and ``channel_id``
would absorb everything in between.
"""

import pytest

from vikingbot.config.schema import SessionKey


def _make_key(type_: str, channel_id: str, chat_id: str) -> SessionKey:
    return SessionKey(type=type_, channel_id=channel_id, chat_id=chat_id)


def test_from_safe_name_roundtrip_simple():
    """The basic round trip still works after the fix."""
    key = _make_key("telegram", "123", "456")
    assert SessionKey.from_safe_name(key.safe_name()) == key


def test_from_safe_name_roundtrip_chat_id_contains_separator():
    """``chat_id`` may itself contain ``__`` — the round trip must preserve it.

    Before the fix, ``split("__")`` produced 4+ parts and the constructor
    would have crashed with IndexError when only 3 fields were unpacked
    from a 4-element list (or, with the original split, silently truncated
    the chat_id and inflated the channel_id).
    """
    key = _make_key("feishu", "cli_a1b2", "thread__42__reply")
    encoded = key.safe_name()
    # encoded should preserve the inner "__" verbatim.
    assert encoded == "feishu__cli_a1b2__thread__42__reply"
    assert SessionKey.from_safe_name(encoded) == key


def test_from_safe_name_roundtrip_chat_id_can_contain_arbitrary_double_underscores():
    """``chat_id`` may contain multiple ``__`` substrings (thread ids etc.)."""
    key = _make_key("discord", "guild-1", "channel-2__thread-3__reply-4")
    encoded = key.safe_name()
    assert encoded == "discord__guild-1__channel-2__thread-3__reply-4"
    # maxsplit=2 leaves the rest of the string (everything after the second
    # separator) as chat_id, so the round trip preserves the full chat_id.
    assert SessionKey.from_safe_name(encoded) == key


def test_from_safe_name_rejects_too_few_parts():
    """safe_name with fewer than 3 '__'-separated parts must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid safe_name"):
        SessionKey.from_safe_name("only_one_part")


def test_from_safe_name_rejects_empty_string():
    """Empty input is not a valid safe_name."""
    with pytest.raises(ValueError, match="Invalid safe_name"):
        SessionKey.from_safe_name("")


def test_from_safe_name_rejects_single_separator():
    """A string with one ``__`` separator has only 2 parts — must raise."""
    with pytest.raises(ValueError, match="Invalid safe_name"):
        SessionKey.from_safe_name("foo__bar")