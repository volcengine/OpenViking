# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Language detection utilities.
"""

import locale
import os
import re
import time
from typing import Callable

from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_CHINESE_MIN_RATIO = 0.05
_STRONG_DOMINANT_RATIO = 0.60
# The Latin-1 letter ranges skip the multiplication and division signs.
_LATIN_LETTERS = r"A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
_LATIN_WORD_RE = re.compile(
    rf"(?<![{_LATIN_LETTERS}\d_])[{_LATIN_LETTERS}]+(?![{_LATIN_LETTERS}\d_])"
)
# URLs and bare import paths do not contribute natural-language text.
_URI_LANGUAGE_NOISE_RE = re.compile(
    r"\b(?:(?:viking|https?)://|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}/)"
    r"[^\s<>\]\)\"'`]+"
)

_LOCALE_LANGUAGE_PREFIXES = {
    "zh": "zh-CN",
    "ja": "ja",
    "ko": "ko",
    "en": "en",
    "chinese": "zh-CN",
    "japanese": "ja",
    "korean": "ko",
    "english": "en",
}

# Use Timezone as a weak fallback signal.
_TIMEZONE_LANGUAGE_GROUPS = {
    "zh-CN": (
        "asia/shanghai",
        "asia/chongqing",
        "asia/harbin",
        "asia/urumqi",
        "asia/hong_kong",
        "asia/macau",
        "asia/taipei",
        "prc",
        "roc",
        "hongkong",
        "china standard time",
        "taipei standard time",
    ),
    "ja": ("asia/tokyo", "japan", "tokyo standard time"),
    "ko": ("asia/seoul", "rok", "korea standard time"),
    "en": (
        "america/new_york",
        "america/chicago",
        "america/denver",
        "america/los_angeles",
        "america/phoenix",
        "america/anchorage",
        "pacific/honolulu",
        "us/eastern",
        "us/central",
        "us/mountain",
        "us/pacific",
        "europe/london",
        "europe/dublin",
        "gb",
        "gb-eire",
        "america/toronto",
        "america/vancouver",
        "canada/eastern",
        "canada/pacific",
        "australia/sydney",
        "australia/melbourne",
        "australia/brisbane",
        "australia/perth",
        "pacific/auckland",
        "nz",
        "eastern standard time",
        "pacific standard time",
        "gmt standard time",
    ),
}

_TIMEZONE_LANGUAGE_HINTS = {
    timezone_name: language
    for language, timezone_names in _TIMEZONE_LANGUAGE_GROUPS.items()
    for timezone_name in timezone_names
}


def _language_from_locale_value(value: str) -> str:
    if not value:
        return ""
    normalized = value.split(":", 1)[0].split(".", 1)[0].split("@", 1)[0]
    normalized = normalized.strip().lower().replace("-", "_")
    if not normalized or normalized in {"c", "posix"}:
        return ""
    prefix = normalized.split("_", 1)[0].split(" ", 1)[0]
    return _LOCALE_LANGUAGE_PREFIXES.get(prefix, "")


def _language_from_timezone_value(value: str) -> str:
    if not value:
        return ""
    normalized = value.strip().lower().lstrip(":")
    if not normalized or normalized == "local":
        return ""
    return _TIMEZONE_LANGUAGE_HINTS.get(normalized, "")


def _language_from_local_timezone() -> str:
    try:
        path_parts = os.path.realpath("/etc/localtime").split(os.sep)
        for marker in ("zoneinfo", "zoneinfo.default"):
            if marker in path_parts:
                timezone_name = "/".join(path_parts[path_parts.index(marker) + 1 :])
                if language := _language_from_timezone_value(timezone_name):
                    return language
    except Exception:
        pass

    for timezone_name in time.tzname:
        if language := _language_from_timezone_value(timezone_name or ""):
            return language
    return ""


def _resolve_system_fallback_language(default_language: str = "en") -> str:
    """Resolve a weak fallback hint from system locale/timezone.

    The result is only used when text detection cannot identify a language.
    Explicit content and output_language_override still take precedence.
    """
    default = (default_language or "en").strip() or "en"
    english_locale_hint = ""

    for env_name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        language = _language_from_locale_value(os.environ.get(env_name, ""))
        if language and language != "en":
            return language
        if language == "en":
            english_locale_hint = language

    try:
        language = _language_from_locale_value(locale.getlocale()[0] or "")
        if language and language != "en":
            return language
        if language == "en":
            english_locale_hint = language
    except Exception:
        pass

    # Honor explicit TZ when set, mainly for Unix-like systems and CI/container environments.
    language = _language_from_timezone_value(os.environ.get("TZ", ""))
    if language:
        return language

    # Fall back to local timezone names; on Windows this may expose Standard Time names via time.tzname.
    language = _language_from_local_timezone()
    if language:
        return language

    if english_locale_hint:
        return english_locale_hint
    return default


def _detect_language_from_text(user_text: str, fallback_language: str) -> str:
    """Detect Chinese, English, Japanese or Korean; weak input uses a supported fallback."""
    fallback = (fallback_language or "").strip()
    if fallback not in _LOCALE_LANGUAGE_PREFIXES.values():
        fallback = "en"
    user_text = strip_language_detection_noise(user_text)

    if not user_text:
        return fallback

    # Unicode script blocks also contain punctuation and digits.
    letters = "".join(filter(str.isalpha, user_text))
    counts = {
        "zh-CN": len(re.findall(r"[\u4e00-\u9fff]", letters)),
        "ja_kana": len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]", letters)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", letters)),
        # Keep these scripts in the ratio denominator for mixed-language text.
        "other": len(re.findall(r"[\u0400-\u04ff\u0600-\u06ff]", letters)),
        # Count Latin words, not the letter fragments of identifiers or field_names.
        "latin": sum(len(word) for word in _LATIN_WORD_RE.findall(user_text)),
    }
    signal_total = sum(counts.values())
    if signal_total == 0:
        return fallback

    if counts["ja_kana"] / signal_total >= _STRONG_DOMINANT_RATIO:
        return "ja"
    if counts["ko"] / signal_total >= _STRONG_DOMINANT_RATIO:
        return "ko"
    if counts["zh-CN"] / signal_total >= _CHINESE_MIN_RATIO:
        return "zh-CN"

    if counts["latin"] or counts["other"]:
        return "en"
    return fallback


def resolve_with_override(
    config, detect: Callable[[], str], *, output_language_override: str | None = None
) -> str:
    """Return config override if set, else call `detect()`.

    The callable returns the detected output language, letting callers choose
    the detector (text vs conversation vs messages) without duplicating the
    override resolution logic. An explicit string takes precedence over config;
    ``""`` enables detection, while ``None`` retains the legacy config lookup.
    """
    if output_language_override is None:
        if config is None:
            config = get_openviking_config()
        output_language_override = getattr(config, "output_language_override", None)
    override = (output_language_override or "").strip()
    if override:
        return override
    return detect()


def resolve_output_language_from_text(
    text: str,
    config=None,
    *,
    fallback_language: str = "en",
    output_language_override: str | None = None,
) -> str:
    """Resolve output language from text with an explicit fallback language.

    Unlike ``resolve_output_language``, this helper does not consult locale or
    timezone. Use it when an empty or low-signal text should not inherit the
    runtime environment language.
    """
    fallback = (fallback_language or "en").strip() or "en"
    return resolve_with_override(
        config,
        lambda: _detect_language_from_text(text, fallback),
        output_language_override=output_language_override,
    )


def strip_language_detection_noise(text: str) -> str:
    """Remove URIs and bare domain paths that should not affect output language."""
    return _URI_LANGUAGE_NOISE_RE.sub(" ", text or "")


def resolve_output_language(
    text: str, config=None, *, output_language_override: str | None = None
) -> str:
    """Resolve output language from text, honoring config override before detection."""
    fallback = _resolve_system_fallback_language("en")
    return resolve_output_language_from_text(
        text,
        config=config,
        fallback_language=fallback,
        output_language_override=output_language_override,
    )


def resolve_output_language_from_conversation(
    conversation: str, config=None, *, output_language_override: str | None = None
) -> str:
    """Resolve output language from a conversation, honoring config override.

    When no override is set, uses `detect_language_from_conversation` which
    scopes detection to user-role content only.
    """
    fallback = _resolve_system_fallback_language("en")
    return resolve_with_override(
        config,
        lambda: detect_language_from_conversation(conversation, fallback),
        output_language_override=output_language_override,
    )


def detect_language_from_conversation(conversation: str, fallback_language: str = "en") -> str:
    """Detect dominant language from user messages in conversation.

    We intentionally scope detection to user role content so assistant/system
    text does not bias the target output language for stored memories.
    """
    fallback = (fallback_language or "en").strip() or "en"

    # Try to extract user messages from conversation string.
    # Supports "[user]: ...", "User: ...", and indexed headers like
    # "[0][user][alice]: ...".
    user_lines = []
    for line in conversation.split("\n"):
        stripped = line.strip()
        line_lower = stripped.lower()
        if line_lower.startswith("[user]:") or line_lower.startswith("user:"):
            content = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
            if content:
                user_lines.append(content)
            continue
        if ":" in stripped:
            header, content = stripped.split(":", 1)
            if re.search(r"\[\s*user\s*\]", header.lower()):
                content = content.strip()
                if content:
                    user_lines.append(content)

    user_text = "\n".join(user_lines)

    # If no user messages found, use the whole conversation as fallback
    if not user_text:
        user_text = conversation

    return _detect_language_from_text(user_text, fallback)
