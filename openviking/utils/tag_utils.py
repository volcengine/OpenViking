# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tag parsing and lightweight extraction helpers."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence

_TAG_SPLIT_RE = re.compile(r"[;,\n]+")
_TAG_NAMESPACE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.+-]{1,31}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,16}")
_CJK_SPLIT_RE = re.compile(r"[的了和与及在中对用为将把等以及、/]+")
_MULTI_DASH_RE = re.compile(r"-{2,}")
USER_TAG_NAMESPACE = "user"
AUTO_TAG_NAMESPACE = "auto"
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


def _normalize_namespace(value: str) -> str:
    namespace = str(value or "").strip().lower()
    if not namespace:
        return ""

    namespace = re.sub(r"[^0-9a-z-]+", "-", namespace)
    namespace = _MULTI_DASH_RE.sub("-", namespace).strip("-.")
    if not _TAG_NAMESPACE_RE.fullmatch(namespace):
        return ""
    return namespace


def _normalize_tag_body(value: str) -> str:
    tag = str(value or "").strip().lower()
    if not tag:
        return ""

    tag = re.sub(r"[\s_/]+", "-", tag)
    tag = re.sub(r"[^0-9a-z\u4e00-\u9fff.+-]+", "-", tag)
    tag = _MULTI_DASH_RE.sub("-", tag).strip("-.+")
    if len(tag) < 2:
        return ""
    return tag


def _normalize_tag(value: str, default_namespace: str | None = None) -> str:
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


def parse_tags(
    tags: str | Sequence[str] | None,
    *,
    default_namespace: str | None = None,
) -> List[str]:
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
        tag = _normalize_tag(item, default_namespace=default_namespace)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def serialize_tags(
    tags: str | Sequence[str] | None,
    *,
    default_namespace: str | None = None,
) -> str | None:
    parsed = parse_tags(tags, default_namespace=default_namespace)
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


def _force_namespace(tags: str | Sequence[str] | None, namespace: str) -> List[str]:
    normalized_namespace = _normalize_namespace(namespace)
    if not normalized_namespace:
        return []

    forced: List[str] = []
    seen = set()
    for tag in parse_tags(tags):
        body = tag.split(":", 1)[1] if ":" in tag else tag
        normalized = f"{normalized_namespace}:{body}"
        if normalized in seen:
            continue
        seen.add(normalized)
        forced.append(normalized)
    return forced


def canonicalize_user_tags(tags: str | Sequence[str] | None) -> List[str]:
    """Canonicalize persisted user-provided tags as ``user:<tag>``."""
    return _force_namespace(tags, USER_TAG_NAMESPACE)


def namespace_tags(tags: str | Sequence[str] | None, namespace: str) -> List[str]:
    """Apply a namespace to every tag body, overriding any existing prefix."""
    return _force_namespace(tags, namespace)


def expand_query_tags(
    tags: str | Sequence[str] | None,
    *,
    namespaces: Sequence[str] = (USER_TAG_NAMESPACE, AUTO_TAG_NAMESPACE),
) -> List[str]:
    """Expand bare query tags across known namespaces for backwards-compatible search."""
    normalized = parse_tags(tags)
    if not normalized:
        return []

    expanded: List[str] = []
    seen = set()
    for tag in normalized:
        candidates = [tag] if ":" in tag else [f"{namespace}:{tag}" for namespace in namespaces]
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


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
        namespace_tags(_extract_path_tags(uri), AUTO_TAG_NAMESPACE),
        namespace_tags(_extract_text_tags(text_values), AUTO_TAG_NAMESPACE),
        max_tags=max_tags,
    )
