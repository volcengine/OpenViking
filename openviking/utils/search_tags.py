# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for resource retrieval tags."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence

USER_SEARCH_TAG_NAMESPACE = "user"
AUTO_SEARCH_TAG_NAMESPACE = "auto"
MAX_SEARCH_TAGS = 8

_SEARCH_TAG_SPLIT_RE = re.compile(r"[;,\n]+")
_SEARCH_TAG_NAMESPACE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")
_SEARCH_TAG_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.+-]{1,31}")
_SEARCH_TAG_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,16}")
_SEARCH_TAG_CJK_SPLIT_RE = re.compile(r"[的了和与及在中对用为将把等以及、/]+")
_SEARCH_TAG_MULTI_DASH_RE = re.compile(r"-{2,}")
_GENERIC_SEGMENTS = {
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


def _normalize_namespace(value: str) -> str:
    namespace = str(value or "").strip().lower()
    if not namespace:
        return ""
    namespace = re.sub(r"[^0-9a-z-]+", "-", namespace)
    namespace = _SEARCH_TAG_MULTI_DASH_RE.sub("-", namespace).strip("-.")
    if not _SEARCH_TAG_NAMESPACE_RE.fullmatch(namespace):
        return ""
    return namespace


def _normalize_tag_body(value: str) -> str:
    tag = str(value or "").strip().lower()
    if not tag:
        return ""
    tag = re.sub(r"[\s_/]+", "-", tag)
    tag = re.sub(r"[^0-9a-z\u4e00-\u9fff.+-]+", "-", tag)
    tag = _SEARCH_TAG_MULTI_DASH_RE.sub("-", tag).strip("-.+")
    if len(tag) < 2:
        return ""
    return tag


def _normalize_search_tag(value: str, default_namespace: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    namespace = ""
    body = raw
    if ":" in raw:
        candidate_namespace, body = raw.split(":", 1)
        namespace = _normalize_namespace(candidate_namespace)
        if not namespace:
            return ""

    normalized_body = _normalize_tag_body(body)
    if not normalized_body:
        return ""

    if not namespace and default_namespace:
        namespace = _normalize_namespace(default_namespace)

    if namespace:
        return f"{namespace}:{normalized_body}"
    return normalized_body


def parse_search_tags(
    value: str | Sequence[str] | None,
    *,
    default_namespace: str | None = None,
) -> List[str]:
    if not value:
        return []

    if isinstance(value, str):
        raw_items = _SEARCH_TAG_SPLIT_RE.split(value)
    else:
        raw_items = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                raw_items.extend(_SEARCH_TAG_SPLIT_RE.split(item))
            else:
                raw_items.append(str(item))

    result: List[str] = []
    seen = set()
    for item in raw_items:
        tag = _normalize_search_tag(item, default_namespace=default_namespace)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def canonicalize_user_tags(value: str | Sequence[str] | None) -> List[str]:
    return [
        tag
        for tag in (
            _normalize_search_tag(item, default_namespace=USER_SEARCH_TAG_NAMESPACE)
            for item in parse_search_tags(value)
        )
        if tag
    ]


def expand_query_tags(
    value: str | Sequence[str] | None,
    *,
    namespaces: Sequence[str] = (USER_SEARCH_TAG_NAMESPACE, AUTO_SEARCH_TAG_NAMESPACE),
) -> List[str]:
    normalized = parse_search_tags(value)
    if not normalized:
        return []

    expanded: List[str] = []
    seen = set()
    for tag in normalized:
        candidates = [tag] if ":" in tag else [f"{ns}:{tag}" for ns in namespaces]
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


def sanitize_search_tags(value: str | Sequence[str] | None) -> List[str]:
    sanitized = []
    seen = set()
    for tag in parse_search_tags(value):
        namespace = tag.split(":", 1)[0] if ":" in tag else ""
        if namespace not in {USER_SEARCH_TAG_NAMESPACE, AUTO_SEARCH_TAG_NAMESPACE}:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        sanitized.append(tag)
    return sanitized[:MAX_SEARCH_TAGS]


def merge_search_tags(
    *sources: str | Sequence[str] | None, max_tags: int = MAX_SEARCH_TAGS
) -> List[str]:
    merged: List[str] = []
    seen = set()
    for source in sources:
        for tag in sanitize_search_tags(source):
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
        normalized = _normalize_tag_body(cleaned)
        if not normalized or normalized in _GENERIC_SEGMENTS:
            continue
        results.append(normalized)
        for token in normalized.split("-"):
            if len(token) >= 3 and token not in _GENERIC_SEGMENTS:
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
            for token in (
                _normalize_tag_body(token) for token in _SEARCH_TAG_WORD_RE.findall(lowered)
            )
            if token and len(token) >= 3 and token not in _STOP_WORDS
        ]
        for token in english_tokens:
            words[token] += 1
        for left, right in zip(english_tokens, english_tokens[1:], strict=False):
            if left == right:
                continue
            bigrams[f"{left}-{right}"] += 1
        for chunk in _SEARCH_TAG_CJK_RE.findall(text):
            for part in _SEARCH_TAG_CJK_SPLIT_RE.split(chunk):
                normalized = _normalize_tag_body(part)
                if normalized:
                    cjk_terms[normalized] += 1

    ranked = [tag for tag, _ in bigrams.most_common(4)]
    ranked.extend(tag for tag, _ in cjk_terms.most_common(4))
    ranked.extend(tag for tag, _ in words.most_common(6))
    return ranked


def extract_context_tags(
    uri: str,
    *,
    texts: Sequence[str] | None = None,
    inherited_tags: str | Sequence[str] | None = None,
    max_tags: int = MAX_SEARCH_TAGS,
) -> List[str]:
    inherited_user_tags = [
        tag
        for tag in sanitize_search_tags(inherited_tags)
        if tag.startswith(f"{USER_SEARCH_TAG_NAMESPACE}:")
    ]
    auto_tags = [
        _normalize_search_tag(tag, default_namespace=AUTO_SEARCH_TAG_NAMESPACE)
        for tag in [*_extract_path_tags(uri), *_extract_text_tags(texts or [])]
    ]
    auto_tags = [tag for tag in auto_tags if tag]
    return merge_search_tags(inherited_user_tags, auto_tags, max_tags=max_tags)
