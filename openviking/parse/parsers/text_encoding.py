# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Text encoding normalization helpers for uploaded text files."""

import codecs
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from charset_normalizer import from_bytes

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


_BOM_ENCODINGS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
    (codecs.BOM_UTF8, "utf-8-sig"),
)

_DETECTION_ENCODINGS = (
    "gb18030",
    "gbk",
    "gb2312",
    "big5",
    "cp950",
    "cp932",
    "shift_jis",
    "euc_jp",
    "euc_kr",
    "cp949",
    "cp1252",
    "latin_1",
)

# These values use codecs.lookup(...).name canonical forms because candidates
# are canonicalized before set membership checks.
_CHINESE_ENCODINGS = {"gb18030", "gbk", "gb2312", "big5", "cp950"}
_CHINESE_ENCODING_PREFERENCE = ("gb18030", "gbk", "gb2312", "big5", "cp950")
_JAPANESE_ENCODINGS = {"cp932", "shift_jis", "euc_jp"}
_KOREAN_ENCODINGS = {"euc_kr", "cp949"}
_CJK_ENCODINGS = _CHINESE_ENCODINGS | _JAPANESE_ENCODINGS | _KOREAN_ENCODINGS
# Very short CJK samples can decode into plausible text under several legacy
# code pages. Require a few script-specific characters before overriding the
# detector's rank with Korean script evidence.
_MIN_STRONG_SCRIPT_CHARS = 4


@dataclass(frozen=True)
class _ScriptProfile:
    cjk: int
    kana: int
    hangul_syllables: int
    hangul_jamo: int


@dataclass(frozen=True)
class _EncodingCandidate:
    encoding: str
    decoded: str
    rank: int
    from_detector: bool


def normalize_text_bytes(content: bytes, file_path: Union[str, Path] = "") -> bytes:
    """Normalize legacy text bytes to UTF-8 while leaving unknown bytes untouched."""
    try:
        return _normalize_text_bytes(content, file_path)
    except Exception as exc:
        logger.warning(f"Encoding detection failed for {file_path}: {exc}")
        return content


def _normalize_text_bytes(content: bytes, file_path: Union[str, Path] = "") -> bytes:
    if not content:
        return content

    bom_normalized = _decode_known_bom(content)
    if bom_normalized is not None:
        return bom_normalized

    try:
        content.decode("utf-8")
        return content
    except UnicodeDecodeError:
        pass

    if _looks_binary(content):
        logger.debug(f"Detected binary-like text file {file_path}, skipping encoding detection")
        return content

    candidate = _choose_candidate(_iter_candidates(content))
    if candidate is None:
        logger.warning(f"Encoding detection failed for {file_path}: no matching encoding found")
        return content

    decoded = candidate.decoded.replace("\x00", "")
    normalized = decoded.encode("utf-8")
    logger.debug(f"Converted {file_path} from {candidate.encoding} to UTF-8")
    return normalized


def _decode_known_bom(content: bytes) -> Optional[bytes]:
    for bom, encoding in _BOM_ENCODINGS:
        if content.startswith(bom):
            try:
                return content.decode(encoding).encode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def _looks_binary(content: bytes) -> bool:
    sample = content[: min(8192, len(content))]
    if not sample:
        return False

    return sample.count(b"\x00") / len(sample) > 0.05


def _iter_candidates(content: bytes) -> Iterable[_EncodingCandidate]:
    seen: set[str] = set()
    rank = 0

    for match in from_bytes(content, cp_isolation=_DETECTION_ENCODINGS):
        candidate = _decode_candidate(content, match.encoding, rank, from_detector=True)
        if candidate is None or candidate.encoding in seen:
            continue
        seen.add(candidate.encoding)
        rank += 1
        yield candidate

    for encoding in _DETECTION_ENCODINGS:
        candidate = _decode_candidate(content, encoding, rank, from_detector=False)
        if candidate is None or candidate.encoding in seen:
            continue
        seen.add(candidate.encoding)
        rank += 1
        yield candidate


def _decode_candidate(
    content: bytes, encoding: Optional[str], rank: int, *, from_detector: bool
) -> Optional[_EncodingCandidate]:
    if not encoding:
        return None

    try:
        canonical = codecs.lookup(encoding).name
        decoded = content.decode(canonical)
    except (LookupError, UnicodeDecodeError):
        return None

    if not _is_valid_text(decoded):
        return None

    return _EncodingCandidate(
        encoding=canonical,
        decoded=decoded,
        rank=rank,
        from_detector=from_detector,
    )


def _is_valid_text(decoded: str) -> bool:
    sample = decoded[:1000]
    if not sample:
        return True

    if "\ufffd" in sample:
        return False

    disallowed_controls = sum(1 for char in sample if _is_disallowed_control(char))
    return disallowed_controls / len(sample) <= 0.05


def _is_disallowed_control(char: str) -> bool:
    codepoint = ord(char)
    return (codepoint < 32 and char not in "\t\n\r") or 0x7F <= codepoint <= 0x9F


def _choose_candidate(candidates: Iterable[_EncodingCandidate]) -> Optional[_EncodingCandidate]:
    candidates = list(candidates)
    if not candidates:
        return None

    consistent = [candidate for candidate in candidates if _is_script_consistent(candidate)]
    if not consistent:
        consistent = candidates

    # Preserve detector rank for Japanese: short kanji-only Shift-JIS has no
    # kana signal, but charset-normalizer still ranks CP932 first.
    first = min(consistent, key=lambda candidate: candidate.rank)
    if first.encoding not in _CJK_ENCODINGS:
        return first

    # Short CJK-only samples can remain ambiguous across Chinese, Japanese, and
    # Korean encodings without source charset or language metadata.
    if first.encoding in _JAPANESE_ENCODINGS:
        return first

    preferred_chinese = _preferred_chinese_candidate(consistent)
    detected_chinese = _preferred_chinese_candidate(consistent, from_detector_only=True)
    for candidate in consistent:
        profile = _script_profile(candidate.decoded)
        if (
            candidate.encoding in _KOREAN_ENCODINGS
            and profile.hangul_syllables >= _MIN_STRONG_SCRIPT_CHARS
            and profile.cjk == 0
        ):
            return candidate

    first_profile = _script_profile(first.decoded)
    # Short Simplified Chinese can be misranked as mixed Hangul/CJK under CP949.
    # Only rescue to Chinese when charset-normalizer also emitted a Chinese
    # candidate and the Korean-looking decode is not Hanja-dominant.
    if (
        first.encoding in _KOREAN_ENCODINGS
        and detected_chinese is not None
        and first_profile.hangul_syllables > 0
        and first_profile.cjk > 0
        and first_profile.hangul_syllables >= first_profile.cjk
    ):
        return detected_chinese

    if first.encoding in _CHINESE_ENCODINGS and preferred_chinese is not None:
        return preferred_chinese

    return first


def _preferred_chinese_candidate(
    candidates: Iterable[_EncodingCandidate],
    *,
    from_detector_only: bool = False,
) -> Optional[_EncodingCandidate]:
    for encoding in _CHINESE_ENCODING_PREFERENCE:
        for candidate in candidates:
            if candidate.encoding == encoding and (
                not from_detector_only or candidate.from_detector
            ):
                return candidate
    return None


def _is_script_consistent(candidate: _EncodingCandidate) -> bool:
    profile = _script_profile(candidate.decoded)

    if candidate.encoding in _CHINESE_ENCODINGS:
        return profile.kana == 0 and profile.hangul_syllables == 0 and profile.hangul_jamo == 0

    if candidate.encoding in _JAPANESE_ENCODINGS:
        return profile.hangul_syllables == 0 and profile.hangul_jamo == 0

    if candidate.encoding in _KOREAN_ENCODINGS:
        return profile.kana == 0 and profile.hangul_jamo == 0

    return True


def _script_profile(decoded: str) -> _ScriptProfile:
    sample = decoded[:1000]
    return _ScriptProfile(
        cjk=sum(1 for char in sample if _is_cjk(char)),
        kana=sum(1 for char in sample if _is_kana(char)),
        hangul_syllables=sum(1 for char in sample if "\uac00" <= char <= "\ud7af"),
        hangul_jamo=sum(1 for char in sample if _is_hangul_jamo(char)),
    )


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff"


def _is_kana(char: str) -> bool:
    return "\u3040" <= char <= "\u30ff" or "\uff66" <= char <= "\uff9f"


def _is_hangul_jamo(char: str) -> bool:
    return (
        "\u1100" <= char <= "\u11ff"
        or "\u3130" <= char <= "\u318f"
        or "\ua960" <= char <= "\ua97f"
        or "\ud7b0" <= char <= "\ud7ff"
    )
