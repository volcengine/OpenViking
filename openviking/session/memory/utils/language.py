# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Language detection utilities.
"""

import re

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _detect_language_from_text(user_text: str, fallback_language: str) -> str:
    """Internal shared helper to detect dominant language from text."""
    fallback = (fallback_language or "en").strip() or "en"

    #return "zh-CN"

    if not user_text:
        return fallback

    # Detect scripts that are largely language-unique first.
    counts = {
        "ko": len(re.findall(r"[\uac00-\ud7af]", user_text)),
        "ru": len(re.findall(r"[\u0400-\u04ff]", user_text)),
        "ar": len(re.findall(r"[\u0600-\u06ff]", user_text)),
    }

    detected, score = max(counts.items(), key=lambda item: item[1])
    if score > 0:
        return detected

    # CJK disambiguation:
    # - Japanese often includes Han characters too, so Han-count alone can
    #   misclassify Japanese as Chinese.
    # - If any Kana is present, prioritize Japanese.
    kana_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]", user_text))
    han_count = len(re.findall(r"[\u4e00-\u9fff]", user_text))

    if kana_count > 0:
        return "ja"
    if han_count > 0:
        return "zh-CN"

    return fallback


def resolve_output_language(text: str, config=None) -> str:
    """Resolve output language, honoring config override before content detection.

    When `output_language_override` is set in OpenViking config, returns it directly
    and skips content-based detection entirely. Otherwise falls back to
    `_detect_language_from_text` using the configured `language_fallback`.
    """
    if config is None:
        from openviking_cli.utils.config import get_openviking_config

        config = get_openviking_config()
    override = (getattr(config, "output_language_override", None) or "").strip()
    if override:
        return override
    fallback = (getattr(config, "language_fallback", None) or "en").strip() or "en"
    return _detect_language_from_text(text, fallback)


def resolve_output_language_from_conversation(conversation: str, config=None) -> str:
    """Resolve output language for a conversation, honoring config override.

    Mirrors `resolve_output_language` but uses `detect_language_from_conversation`
    (user-only extraction) when no override is set.
    """
    if config is None:
        from openviking_cli.utils.config import get_openviking_config

        config = get_openviking_config()
    override = (getattr(config, "output_language_override", None) or "").strip()
    if override:
        return override
    fallback = (getattr(config, "language_fallback", None) or "en").strip() or "en"
    return detect_language_from_conversation(conversation, fallback_language=fallback)


def detect_language_from_conversation(conversation: str, fallback_language: str = "en") -> str:
    """Detect dominant language from user messages in conversation.

    We intentionally scope detection to user role content so assistant/system
    text does not bias the target output language for stored memories.
    """
    fallback = (fallback_language or "en").strip() or "en"

    # Try to extract user messages from conversation string
    # Look for patterns like "[user]: ..." or "User: ..."
    user_lines = []
    for line in conversation.split("\n"):
        line_lower = line.strip().lower()
        if line_lower.startswith("[user]:") or line_lower.startswith("user:"):
            # Extract content after the role marker
            content = line.split(":", 1)[1].strip() if ":" in line else line.strip()
            if content:
                user_lines.append(content)

    user_text = "\n".join(user_lines)

    # If no user messages found, use the whole conversation as fallback
    if not user_text:
        user_text = conversation

    return _detect_language_from_text(user_text, fallback)
