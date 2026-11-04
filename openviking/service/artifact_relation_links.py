# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract page links and resolve their paths without filesystem access."""

from __future__ import annotations

import hashlib
import posixpath
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from openviking.service.artifact_relations_models import RelationSource
from openviking.utils.path_safety import normalize_storage_target_uri

PARSER_VERSION = "markdown-v1"

_markdown = MarkdownIt("commonmark")
# Preserve destination spelling; only Markdown escapes/entities are interpreted.
# In particular, do not URL-encode Unicode or resolve relative paths here.
_markdown.normalizeLink = lambda href: href


def git_blob_oid(content: bytes) -> str:
    """Match ragfs's SHA-1 Git blob hash of the exact final bytes."""
    digest = hashlib.sha1(f"blob {len(content)}\0".encode("ascii"), usedforsecurity=False)
    digest.update(content)
    return digest.hexdigest()


def extract_relation_source(content: bytes) -> RelationSource:
    """Keep raw Markdown destinations, including external links and broken links.

    Images, code and raw HTML are not Markdown page links. Duplicate destinations
    are stored once in document order. Invalid UTF-8 fails instead of publishing
    replacement characters as a successful extraction.
    """
    text = content.decode("utf-8")
    links = dict.fromkeys(
        href
        for token in _markdown.parse(text)
        for child in token.children or ()
        if child.type == "link_open" and (href := child.attrGet("href")) is not None
    )
    return RelationSource(blob_oid=git_blob_oid(content), links=list(links))


def resolve_relation_link(source_uri: str, href: str, *, target_root: str) -> str | None:
    """Resolve a destination within one target; existence is checked by the builder.

    Root-relative links start at the account's Viking namespace root. The result
    is a storage URI without a trailing slash; the builder maps directories to
    their slash-terminated node IDs. Pure anchors and external links yield None.
    """
    root = normalize_storage_target_uri(target_root)
    source = normalize_storage_target_uri(source_uri)
    if source != root and not source.startswith(f"{root}/"):
        raise ValueError(f"Source page is outside Compile target: {source_uri}")

    try:
        # Split before percent decoding: encoded '#' and '?' belong to the path.
        parsed = urlsplit(href.strip())
        if parsed.scheme and parsed.scheme != "viking":
            return None
        if parsed.netloc and not parsed.scheme:
            return None  # Protocol-relative external URL.
        if not parsed.path and not parsed.netloc:
            return None
        segments = []
        for segment in parsed.path.split("/"):
            decoded = unquote(segment, errors="strict")
            # Match storage rules: encoded separators cannot create path segments.
            if decoded != segment and (decoded in {".", ".."} or "/" in decoded or "\\" in decoded):
                return None
            segments.append(decoded)
        path = "/".join(segments)
        if parsed.scheme == "viking":
            if not parsed.netloc:
                return None
            path = f"{parsed.netloc}/{path.lstrip('/')}"
        elif not path.startswith("/"):
            parent = posixpath.dirname(source.removeprefix("viking://"))
            path = posixpath.join(parent, path)
        normalized = normalize_storage_target_uri(
            f"viking://{posixpath.normpath(path.lstrip('/'))}"
        )
    except (ValueError, UnicodeError):
        return None
    if normalized == root or normalized.startswith(f"{root}/"):
        return normalized
    return None
