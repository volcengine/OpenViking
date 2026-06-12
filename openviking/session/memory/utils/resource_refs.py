# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for resource references embedded in memory content."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from openviking.session.memory.dataclass import MemoryFile

RESOURCE_REF_SOURCE_CONTENT_WRITE = "content.write"
RESOURCE_REF_SOURCE_SESSION_COMMIT = "session.commit"

_RESOURCE_URI_PATH_CHARS = r"[^\s<>\]\)\"'，。；：！？、,;:!?）】》]+"
_RESOURCE_URI_BOUNDARY = r"(?=$|[\s<>\]\)\"'，。；：！？、,;:!?.）】》])"
_RESOURCE_URI_PATTERN = (
    r"viking://(?:"
    rf"resources(?:/{_RESOURCE_URI_PATH_CHARS})?"
    r"|user/[^/\s<>\]\)\"']+/(?:"
    rf"resources(?:/{_RESOURCE_URI_PATH_CHARS})?"
    rf"|peers/[^/\s<>\]\)\"']+/resources(?:/{_RESOURCE_URI_PATH_CHARS})?"
    r")"
    r")"
    rf"{_RESOURCE_URI_BOUNDARY}"
)
_MARKDOWN_RESOURCE_LINK_RE = re.compile(rf"\[([^\]\n]+)\]\(({_RESOURCE_URI_PATTERN})\)")
_RESOURCE_URI_RE = re.compile(_RESOURCE_URI_PATTERN)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_TRAILING_URI_PUNCTUATION = ".,;:!?，。；：！？、）】》"
_SENTENCE_BOUNDARIES = "。！？.!?\n"
_MAX_LINKIFIED_SENTENCE_CHARS = 160
_RESOURCE_CLEANUP_ARTIFACT_LINE_RE = re.compile(
    r"(?m)^(?:None ChatLog:|Original reason:\s*|Memory URI:\s*viking://user/[^\n]*)\n?"
)
_RESOURCE_URI_MARKER_RE = re.compile(
    r"[，,；;：:\s]*(?:资源\s*URI\s*为|资源\s*URI|Resource\s+URI)\s*[:：为]?\s*$",
    re.IGNORECASE,
)


def sync_memory_resource_refs(
    mf: MemoryFile,
    *,
    source: str,
    reason: Optional[str] = None,
    created_at: Optional[str] = None,
) -> bool:
    """Link visible resource URIs and keep MEMORY_FIELDS.resource_refs in sync."""
    before_content = mf.content
    before_refs = _coerce_resource_refs(mf.extra_fields.get("resource_refs"))

    code_spans = _protected_code_spans(mf.content)
    markdown_refs, markdown_spans = _extract_markdown_resource_refs(
        mf.content,
        code_spans,
    )
    mf.content, bare_refs = _linkify_bare_resource_uris(
        mf.content,
        code_spans + markdown_spans,
    )
    _merge_resource_refs(
        mf,
        markdown_refs + bare_refs,
        source=source,
        reason=reason,
        created_at=created_at,
    )

    after_refs = _coerce_resource_refs(mf.extra_fields.get("resource_refs"))
    return before_content != mf.content or before_refs != after_refs


def coerce_resource_refs(value: Any) -> List[Dict[str, Any]]:
    return _coerce_resource_refs(value)


def contains_resource_uri(content: str) -> bool:
    """Return whether text contains any supported resource URI form."""
    return bool(_RESOURCE_URI_RE.search(content or ""))


def content_references_resource(
    content: str,
    resource_uri: str,
    *,
    recursive: bool = False,
) -> bool:
    """Return whether visible memory content references a resource URI."""
    return any(
        resource_ref_matches(uri, resource_uri, recursive=recursive)
        for uri in extract_resource_uris(content)
    )


def extract_resource_uris(content: str) -> List[str]:
    """Extract visible resource URIs from markdown links or bare URI text."""
    uris: List[str] = []
    for match in _MARKDOWN_RESOURCE_LINK_RE.finditer(content or ""):
        uri = _trim_resource_uri(match.group(2).strip())
        if uri:
            uris.append(uri)
    for match in _RESOURCE_URI_RE.finditer(content or ""):
        uri = _trim_resource_uri(match.group(0))
        if uri:
            uris.append(uri)
    return list(dict.fromkeys(uris))


def remove_resource_references_from_memory(
    mf: MemoryFile,
    resource_uri: str,
    *,
    recursive: bool = False,
) -> bool:
    """Remove visible references and MEMORY_FIELDS.resource_refs for one resource."""
    before_content = mf.content
    before_refs = _coerce_resource_refs(mf.extra_fields.get("resource_refs"))

    mf.content = remove_resource_references_from_content(
        mf.content,
        resource_uri,
        recursive=recursive,
    )
    refs = [
        ref
        for ref in before_refs
        if not resource_ref_matches(ref.get("resource_uri"), resource_uri, recursive=recursive)
    ]
    if refs:
        mf.extra_fields["resource_refs"] = refs
    else:
        mf.extra_fields.pop("resource_refs", None)

    return before_content != mf.content or before_refs != refs


def remove_resource_references_from_content(
    content: str,
    resource_uri: str,
    *,
    recursive: bool = False,
) -> str:
    """Remove sentences/list lines that contain matching resource URI references."""
    text = content or ""
    spans = _matching_resource_reference_spans(text, resource_uri, recursive=recursive)
    if not spans:
        return text

    sentence_spans = _merge_spans(
        _expand_to_sentence_span(text, start, end) for start, end in spans
    )
    for start, end in reversed(sentence_spans):
        text = text[:start] + text[end:]
    return _normalize_removed_reference_text(text)


def resource_ref_matches(
    ref_uri: Any,
    target_uri: str,
    *,
    recursive: bool,
) -> bool:
    if not isinstance(ref_uri, str) or not ref_uri:
        return False
    normalized_ref = _trim_resource_uri(ref_uri).rstrip("/")
    normalized_target = _trim_resource_uri(target_uri).rstrip("/")
    if normalized_ref == normalized_target:
        return True
    return recursive and normalized_ref.startswith(normalized_target + "/")


def _protected_code_spans(content: str) -> List[tuple[int, int]]:
    spans = [(match.start(), match.end()) for match in _CODE_BLOCK_RE.finditer(content or "")]
    spans.extend((match.start(), match.end()) for match in _INLINE_CODE_RE.finditer(content or ""))
    return spans


def _extract_markdown_resource_refs(
    content: str,
    protected_spans: Sequence[tuple[int, int]],
) -> tuple[List[Dict[str, Any]], List[tuple[int, int]]]:
    refs: List[Dict[str, Any]] = []
    link_spans: List[tuple[int, int]] = []
    for match in _MARKDOWN_RESOURCE_LINK_RE.finditer(content or ""):
        if _overlaps_spans(match.start(), match.end(), protected_spans):
            continue
        label = match.group(1).strip()
        resource_uri = _trim_resource_uri(match.group(2).strip())
        link_spans.append((match.start(), match.end()))
        refs.append(
            {
                "resource_uri": resource_uri,
                "match_text": label or None,
            }
        )
    return refs, link_spans


def _linkify_bare_resource_uris(
    content: str,
    protected_spans: Sequence[tuple[int, int]],
) -> tuple[str, List[Dict[str, Any]]]:
    refs: List[Dict[str, Any]] = []
    updated = content or ""
    covered_start = len(updated) + 1

    matches = list(_RESOURCE_URI_RE.finditer(updated))
    for match in reversed(matches):
        resource_uri = _trim_resource_uri(match.group(0))
        if not resource_uri:
            continue
        start = match.start()
        end = start + len(resource_uri)
        if _overlaps_spans(start, end, protected_spans):
            continue

        refs.append({"resource_uri": resource_uri})
        sentence_span = _previous_sentence_span(updated, start)
        if not sentence_span:
            continue
        sentence_start, sentence_end = sentence_span
        if end > covered_start:
            continue
        anchor_start = sentence_start
        anchor_end = sentence_end
        anchor = updated[anchor_start:anchor_end]
        marker_span = _resource_uri_marker_span(anchor)
        if marker_span:
            label_span = _resource_clause_span_before_marker(
                updated,
                sentence_start,
                sentence_start + marker_span[0],
            )
            if label_span:
                anchor_start, anchor_end = label_span
                anchor = updated[anchor_start:anchor_end]
        if contains_resource_uri(anchor) or "](" in anchor:
            continue
        refs[-1]["match_text"] = anchor
        replacement = f"[{anchor}]({resource_uri})"
        updated = updated[:anchor_start] + replacement + updated[end:]
        covered_start = anchor_start

    refs.reverse()
    return updated, refs


def _previous_sentence_span(content: str, uri_start: int) -> Optional[tuple[int, int]]:
    sentence_end = uri_start
    while sentence_end > 0 and content[sentence_end - 1].isspace():
        sentence_end -= 1
    if sentence_end <= 0:
        return None

    boundary_search_end = sentence_end
    if content[sentence_end - 1] in _SENTENCE_BOUNDARIES:
        boundary_search_end = sentence_end - 1
    sentence_start = 0
    for idx in range(boundary_search_end - 1, -1, -1):
        if content[idx] in _SENTENCE_BOUNDARIES:
            sentence_start = idx + 1
            break
    while sentence_start < sentence_end and content[sentence_start].isspace():
        sentence_start += 1

    anchor = content[sentence_start:sentence_end]
    if not anchor or len(anchor) > _MAX_LINKIFIED_SENTENCE_CHARS:
        return None
    return sentence_start, sentence_end


def _merge_resource_refs(
    mf: MemoryFile,
    refs: Sequence[Dict[str, Any]],
    *,
    source: str,
    reason: Optional[str],
    created_at: Optional[str],
) -> None:
    visible_refs: Dict[str, Dict[str, Any]] = {}
    for ref in refs:
        resource_uri = ref.get("resource_uri")
        if not isinstance(resource_uri, str) or not resource_uri:
            continue
        existing = visible_refs.setdefault(resource_uri, {"resource_uri": resource_uri})
        match_text = ref.get("match_text")
        if match_text and not existing.get("match_text"):
            existing["match_text"] = match_text

    existing_refs = _coerce_resource_refs(mf.extra_fields.get("resource_refs"))
    merged: List[Dict[str, Any]] = []
    seen_resource_uris: set[str] = set()
    ref_created_at = created_at or datetime.now(timezone.utc).isoformat()

    for existing_ref in existing_refs:
        resource_uri = existing_ref.get("resource_uri")
        if not isinstance(resource_uri, str) or not resource_uri:
            merged.append(existing_ref)
            continue

        visible_ref = visible_refs.get(resource_uri)
        if existing_ref.get("source") == source and visible_ref is None:
            continue

        if visible_ref and existing_ref.get("source") == source:
            if visible_ref.get("match_text"):
                existing_ref["match_text"] = visible_ref["match_text"]
            existing_ref.setdefault("created_at", ref_created_at)
            if reason:
                existing_ref.setdefault("reason", reason)

        merged.append(existing_ref)
        seen_resource_uris.add(resource_uri)

    for resource_uri, visible_ref in visible_refs.items():
        if resource_uri in seen_resource_uris:
            continue
        ref = {
            "resource_uri": resource_uri,
            "source": source,
            "created_at": ref_created_at,
        }
        if reason:
            ref["reason"] = reason
        if visible_ref.get("match_text"):
            ref["match_text"] = visible_ref["match_text"]
        merged.append(ref)

    if merged:
        mf.extra_fields["resource_refs"] = merged
    else:
        mf.extra_fields.pop("resource_refs", None)


def _coerce_resource_refs(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def _trim_resource_uri(resource_uri: str) -> str:
    return (resource_uri or "").rstrip(_TRAILING_URI_PUNCTUATION)


def _matching_resource_reference_spans(
    content: str,
    resource_uri: str,
    *,
    recursive: bool,
) -> List[tuple[int, int]]:
    spans: List[tuple[int, int]] = []
    markdown_spans: List[tuple[int, int]] = []
    for match in _MARKDOWN_RESOURCE_LINK_RE.finditer(content or ""):
        markdown_spans.append((match.start(), match.end()))
        if resource_ref_matches(match.group(2), resource_uri, recursive=recursive):
            spans.append((match.start(), match.end()))

    for match in _RESOURCE_URI_RE.finditer(content or ""):
        resource_end = match.start() + len(_trim_resource_uri(match.group(0)))
        if _overlaps_spans(match.start(), resource_end, markdown_spans):
            continue
        if resource_ref_matches(match.group(0), resource_uri, recursive=recursive):
            spans.append((match.start(), resource_end))
    return spans


def _resource_uri_marker_span(anchor: str) -> Optional[tuple[int, int]]:
    match = _RESOURCE_URI_MARKER_RE.search(anchor)
    if not match:
        return None
    return match.start(), match.end()


def _resource_clause_span_before_marker(
    content: str,
    sentence_start: int,
    marker_start: int,
) -> Optional[tuple[int, int]]:
    prefix = content[sentence_start:marker_start].rstrip("，,；;：: ")
    if not prefix:
        return None

    pieces = list(re.finditer(r"[^，,；;。.!?？]+$", prefix))
    if not pieces:
        return None
    label_start = sentence_start + pieces[-1].start()
    label_end = sentence_start + pieces[-1].end()
    if _valid_resource_clause(content[label_start:label_end]):
        return label_start, label_end
    return None


def _valid_resource_clause(clause: str) -> bool:
    clause = clause.strip()
    return bool(clause) and len(clause) <= 120 and "\n" not in clause and "](" not in clause


def _expand_to_sentence_span(content: str, start: int, end: int) -> tuple[int, int]:
    span_start = start
    for idx in range(start - 1, -1, -1):
        if content[idx] in _SENTENCE_BOUNDARIES:
            span_start = idx + 1
            break
    else:
        span_start = 0

    span_end = end
    for idx in range(end, len(content)):
        if content[idx] in _SENTENCE_BOUNDARIES:
            span_end = idx + 1
            break
    else:
        span_end = len(content)

    while span_start < span_end and content[span_start].isspace():
        span_start += 1
    while span_end < len(content) and content[span_end].isspace():
        span_end += 1
    return span_start, span_end


def _merge_spans(spans: Iterable[tuple[int, int]]) -> List[tuple[int, int]]:
    merged: List[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _normalize_removed_reference_text(content: str) -> str:
    content = _RESOURCE_CLEANUP_ARTIFACT_LINE_RE.sub("", content)
    text = re.sub(r"[ \t]+([，。；：！？,.!?;:])", r"\1", content)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _overlaps_spans(
    start: int,
    end: int,
    protected_spans: Sequence[tuple[int, int]],
) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in protected_spans)
