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

_SCRIPT_MIN_CHARS = 2
_SCRIPT_MIN_RATIO = 0.20
_JAPANESE_KANA_MIN_CHARS = 3
_STRONG_DOMINANT_MIN_CHARS = 10
_STRONG_DOMINANT_RATIO = 0.95
_PRIMARY_LANGUAGES = {"zh-CN", "en"}

_LATIN_STOPWORDS = {
    "en": set(
        "a an and are as be document for from in is of on please project that the this to user with".split()
    ),
    "it": set(
        "che con da del della di documento e il la le non per preferenze progetto questo questa un una utente".split()
    ),
    "fr": set(
        "avec ce cette de des document du et la le les pour préférences projet que un une utilisateur".split()
    ),
    "es": set(
        "con de del documento el esta este la las los para preferencias proyecto que un una usuario y".split()
    ),
    "de": set(
        "benutzer das der die diese dieser dokument ein eine für ist mit nicht projekt und zu".split()
    ),
    "pt": set(
        "a as com da de do documento e este esta o os para preferências preferencias projeto que um uma usuário usuario".split()
    ),
}

_LATIN_ACCENT_BONUSES = {
    "it": r"[àèéìòù]",
    "fr": r"[àâæçéèêëîïôœùûüÿ]",
    "es": r"[áéíóúüñ¿¡]",
    "de": r"[äöüß]",
    "pt": r"[áâãàçéêíóôõú]",
}
_LATIN_HINT_LANGUAGES = {"it", "fr", "es", "de", "pt"}

_LOCALE_LANGUAGE_PREFIXES = dict(
    zh="zh-CN", ja="ja", ko="ko", ru="ru", ar="ar",
    it="it", fr="fr", es="es", de="de", pt="pt", en="en",
    chinese="zh-CN", japanese="ja", korean="ko", russian="ru", arabic="ar",
    italian="it", french="fr", spanish="es", german="de", portuguese="pt", english="en",
)

# Use Timezone as a weak fallback signal.
_TIMEZONE_LANGUAGE_GROUPS = {
    "zh-CN": (
        "asia/shanghai", "asia/chongqing", "asia/harbin", "asia/urumqi",
        "asia/hong_kong", "asia/macau", "asia/taipei", "prc", "roc", "hongkong",
        "china standard time", "taipei standard time",
    ),
    "ja": ("asia/tokyo", "japan", "tokyo standard time"),
    "ko": ("asia/seoul", "rok", "korea standard time"),
    "ru": (
        "europe/moscow", "europe/kaliningrad", "asia/yekaterinburg", "asia/vladivostok",
        "russian standard time",
    ),
    "ar": (
        "asia/riyadh", "asia/dubai", "asia/qatar", "asia/kuwait",
        "asia/baghdad", "africa/cairo", "africa/algiers", "africa/tunis",
        "arab standard time", "arabian standard time", "egypt standard time",
    ),
    "it": ("europe/rome",),
    "fr": ("europe/paris",),
    "es": ("europe/madrid",),
    "de": ("europe/berlin",),
    "pt": ("europe/lisbon", "america/sao_paulo"),
    "en": (
        "america/new_york", "america/chicago", "america/denver", "america/los_angeles",
        "america/phoenix", "america/anchorage", "pacific/honolulu", "us/eastern",
        "us/central", "us/mountain", "us/pacific", "europe/london", "europe/dublin",
        "gb", "gb-eire", "america/toronto", "america/vancouver", "canada/eastern",
        "canada/pacific", "australia/sydney", "australia/melbourne",
        "australia/brisbane", "australia/perth", "pacific/auckland", "nz",
        "eastern standard time", "pacific standard time", "gmt standard time",
    ),
}

_TIMEZONE_LANGUAGE_HINTS = {
    timezone_name: language
    for language, timezone_names in _TIMEZONE_LANGUAGE_GROUPS.items()
    for timezone_name in timezone_names
}


def _passes_threshold(count: int, total: int) -> bool:
    return count >= _SCRIPT_MIN_CHARS and total > 0 and count / total >= _SCRIPT_MIN_RATIO


def _language_allowed_by_fallback(language: str, fallback_language: str) -> bool:
    return language in _PRIMARY_LANGUAGES or language == fallback_language


def _is_strong_dominant(count: int, total: int) -> bool:
    return count >= _STRONG_DOMINANT_MIN_CHARS and total > 0 and count / total >= _STRONG_DOMINANT_RATIO


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


def _detect_latin_language(text: str, fallback_language: str) -> str:
    """Best-effort detector for common Latin-script languages.

    This intentionally stays conservative: if the signal is weak or tied, it
    uses English instead of guessing a non-English Latin language.
    """
    words = re.findall(r"[a-z\u00c0-\u024f]+", text.lower())
    if len(words) < 3:
        return "en"

    stopword_scores = {
        lang: sum(1 for word in words if word in stopwords)
        for lang, stopwords in _LATIN_STOPWORDS.items()
    }
    scores = dict(stopword_scores)

    lowered = text.lower()
    accent_hits = {}
    for lang, pattern in _LATIN_ACCENT_BONUSES.items():
        accent_hits[lang] = len(re.findall(pattern, lowered))
        scores[lang] += accent_hits[lang]

    language, score = max(scores.items(), key=lambda item: item[1])
    second_score = max((value for key, value in scores.items() if key != language), default=0)
    if language == "en" and score >= 2 and score > second_score:
        return "en"
    if language in _LATIN_HINT_LANGUAGES and len(words) >= 6:
        strong_hint = accent_hits.get(language, 0) > 0 or stopword_scores[language] >= 4
        strong_latin = len(words) >= 20 and score >= 6 and score >= scores.get("en", 0) + 4
        if (
            strong_hint
            and score >= 3
            and score >= scores.get("en", 0) + 2
            and (_language_allowed_by_fallback(language, fallback_language) or strong_latin)
        ):
            return language
    return "en"


def _detect_language_from_text(user_text: str, fallback_language: str) -> str:
    """Internal shared helper to detect dominant language from text."""
    fallback = (fallback_language or "en").strip() or "en"

    if not user_text:
        return fallback

    counts = {
        "zh-CN": len(re.findall(r"[\u4e00-\u9fff]", user_text)),
        "ja_kana": len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]", user_text)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", user_text)),
        "ru": len(re.findall(r"[\u0400-\u04ff]", user_text)),
        "ar": len(re.findall(r"[\u0600-\u06ff]", user_text)),
        "latin": len(re.findall(r"[A-Za-z\u00c0-\u024f]", user_text)),
    }
    signal_total = sum(counts.values())
    if signal_total == 0:
        return fallback

    japanese_total = counts["zh-CN"] + counts["ja_kana"]
    strong_japanese = (
        counts["ja_kana"] >= _STRONG_DOMINANT_MIN_CHARS
        and japanese_total / signal_total >= _STRONG_DOMINANT_RATIO
        and counts["ja_kana"] / japanese_total >= 0.30
    )
    if counts["ja_kana"] >= _JAPANESE_KANA_MIN_CHARS and (
        _language_allowed_by_fallback("ja", fallback) or strong_japanese
    ):
        return "ja"

    non_latin_candidates = {
        "zh-CN": counts["zh-CN"],
        "ko": counts["ko"],
        "ru": counts["ru"],
        "ar": counts["ar"],
    }
    language, score = max(non_latin_candidates.items(), key=lambda item: item[1])
    if _passes_threshold(score, signal_total) and (
        _language_allowed_by_fallback(language, fallback)
        or _is_strong_dominant(score, signal_total)
    ):
        return language

    if counts["latin"] > 0:
        return _detect_latin_language(user_text, fallback)
    return fallback


def resolve_with_override(config, detect: Callable[[], str]) -> str:
    """Return config override if set, else call `detect()`.

    The callable returns the detected output language, letting callers choose
    the detector (text vs conversation vs messages) without duplicating the
    override resolution logic.
    """
    if config is None:
        config = get_openviking_config()
    override = (getattr(config, "output_language_override", None) or "").strip()
    if override:
        return override
    return detect()


def resolve_output_language(text: str, config=None) -> str:
    """Resolve output language from text, honoring config override before detection."""
    fallback = _resolve_system_fallback_language("en")
    return resolve_with_override(config, lambda: _detect_language_from_text(text, fallback))


def resolve_output_language_from_conversation(conversation: str, config=None) -> str:
    """Resolve output language from a conversation, honoring config override.

    When no override is set, uses `detect_language_from_conversation` which
    scopes detection to user-role content only.
    """
    fallback = _resolve_system_fallback_language("en")
    return resolve_with_override(
        config, lambda: detect_language_from_conversation(conversation, fallback)
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
