"""Tag parsing and lightweight extraction helpers."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence

_TAG_SPLIT_RE = re.compile(r"[;,\n]+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.+-]{1,31}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,16}")
_CJK_SPLIT_RE = re.compile(r"[的了和与及在中对用为将把等以及、/]+")
_MULTI_DASH_RE = re.compile(r"-{2,}")
_GENERIC_PATH_SEGMENTS = {
    "agent",
    "agents",
    "default",
    "memories",
    "resource",
    "resources",
    "session",
    "sessions",
    "skill",
    "skills",
    "user",
    "users",
    "viking",
}
_STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "best",
    "build",
    "content",
    "demo",
    "details",
    "document",
    "documents",
    "example",
    "examples",
    "feature",
    "features",
    "file",
    "files",
    "first",
    "for",
    "from",
    "guide",
    "how",
    "into",
    "just",
    "more",
    "overview",
    "project",
    "related",
    "resource",
    "resources",
    "sample",
    "summary",
    "test",
    "testing",
    "that",
    "the",
    "their",
    "them",
    "these",
    "this",
    "using",
    "with",
}


def _normalize_tag(value: str) -> str:
    tag = str(value or "").strip().lower()
    if not tag:
        return ""

    tag = re.sub(r"[\s_/]+", "-", tag)
    tag = re.sub(r"[^0-9a-z\u4e00-\u9fff.+-]+", "-", tag)
    tag = _MULTI_DASH_RE.sub("-", tag).strip("-.+")
    if len(tag) < 2:
        return ""
    return tag


def parse_tags(tags: str | Sequence[str] | None) -> List[str]:
    """Parse semicolon-delimited tags or a string sequence into normalized tags."""
    if not tags:
        return []

    if isinstance(tags, str):
        raw_items = _TAG_SPLIT_RE.split(tags)
    else:
        raw_items = []
        for item in tags:
            if item is None:
                continue
            if isinstance(item, str):
                raw_items.extend(_TAG_SPLIT_RE.split(item))
            else:
                raw_items.append(str(item))

    seen = set()
    normalized: List[str] = []
    for item in raw_items:
        tag = _normalize_tag(item)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def serialize_tags(tags: str | Sequence[str] | None) -> str | None:
    parsed = parse_tags(tags)
    if not parsed:
        return None
    return ";".join(parsed)


def merge_tags(*sources: str | Sequence[str] | None, max_tags: int = 8) -> List[str]:
    merged: List[str] = []
    seen = set()
    for source in sources:
        for tag in parse_tags(source):
            if tag in seen:
                continue
            merged.append(tag)
            seen.add(tag)
            if len(merged) >= max_tags:
                return merged
    return merged


def _extract_path_tags(uri: str) -> List[str]:
    raw_path = str(uri or "").removeprefix("viking://")
    if not raw_path:
        return []

    results: List[str] = []
    for segment in raw_path.split("/"):
        cleaned = segment.strip()
        if not cleaned or cleaned.startswith("."):
            continue
        cleaned = cleaned.rsplit(".", 1)[0]
        normalized = _normalize_tag(cleaned)
        if not normalized or normalized in _GENERIC_PATH_SEGMENTS:
            continue
        results.append(normalized)
        for token in normalized.split("-"):
            if len(token) >= 3 and token not in _GENERIC_PATH_SEGMENTS:
                results.append(token)
    return results


def _extract_text_tags(texts: Iterable[str]) -> List[str]:
    words: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    cjk_terms: Counter[str] = Counter()

    for text in texts:
        if not text:
            continue

        lowered = text.lower()
        english_tokens = [
            token
            for token in (_normalize_tag(token) for token in _WORD_RE.findall(lowered))
            if token and len(token) >= 3 and token not in _STOP_WORDS
        ]
        for token in english_tokens:
            words[token] += 1
        for left, right in zip(english_tokens, english_tokens[1:], strict=False):
            if left == right:
                continue
            bigrams[f"{left}-{right}"] += 1

        for chunk in _CJK_RE.findall(text):
            for part in _CJK_SPLIT_RE.split(chunk):
                normalized = _normalize_tag(part)
                if normalized:
                    cjk_terms[normalized] += 1

    ranked = [tag for tag, _ in bigrams.most_common(4)]
    ranked.extend(tag for tag, _ in cjk_terms.most_common(4))
    ranked.extend(tag for tag, _ in words.most_common(6))
    return ranked


def extract_context_tags(
    uri: str,
    texts: Sequence[str] | None = None,
    inherited_tags: str | Sequence[str] | None = None,
    max_tags: int = 8,
) -> List[str]:
    """Build conservative tags from path segments, text keywords, and inherited tags."""
    text_values = [text for text in (texts or []) if text]
    return merge_tags(
        inherited_tags,
        _extract_path_tags(uri),
        _extract_text_tags(text_values),
        max_tags=max_tags,
    )
