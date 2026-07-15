# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Link merge and dedup logic for MEMORY_FIELDS links field.

Dedup key: from_uri + to_uri + match_text
Merge rules:
- Weight conflict: take max
- link_type and description: latest write wins
"""

from typing import Any, Dict, List

from openviking.core.namespace import canonical_user_root, context_type_for_uri, uri_parts
from openviking.core.peer_id import safe_peer_id
from openviking_cli.utils.config import get_openviking_config


def wiki_links_enabled() -> bool:
    memory = get_openviking_config().memory
    return bool(memory and memory.link_enabled)


def _memory_type(uri: str) -> str:
    parts = uri_parts(uri)
    try:
        index = parts.index("memories")
    except ValueError:
        return ""
    return parts[index + 1] if len(parts) > index + 1 else ""


def _content_namespace(uri: str, context_type: str) -> str:
    segment = "/resources/" if context_type == "resource" else "/memories/"
    return uri.split(segment, 1)[0] if segment in uri else ""


def _is_resource_uri(uri: str, context_type: str) -> bool:
    return context_type == "resource" and (
        uri.startswith("viking://resources/") or "/resources/" in uri
    )


def is_allowed_wiki_link(
    from_uri: str,
    to_uri: str,
    link_type: str | None = None,
    *,
    ctx: Any = None,
) -> bool:
    """Enforce Wiki direction while preserving legacy resource references."""
    if not from_uri or not to_uri or from_uri == to_uri:
        return False

    from_context = context_type_for_uri(from_uri)
    to_context = context_type_for_uri(to_uri)
    from_memory_type = _memory_type(from_uri)
    to_memory_type = _memory_type(to_uri)
    from_resource = _is_resource_uri(from_uri, from_context)
    to_resource = _is_resource_uri(to_uri, to_context)

    if link_type == "references_resource":
        return from_context == "memory" and to_resource

    if from_resource or to_resource:
        same_namespace = _content_namespace(from_uri, from_context) == _content_namespace(
            to_uri, to_context
        )
        peer_id = safe_peer_id(getattr(ctx, "actor_peer_id", None)) if ctx else None
        if ctx is not None and (from_uri.startswith("viking://resources/") or peer_id):
            target_namespace = canonical_user_root(ctx)
            if peer_id:
                target_namespace = f"{target_namespace}/peers/{peer_id}"
            same_namespace = _content_namespace(to_uri, to_context) == target_namespace
        return (
            from_resource
            and from_uri.rstrip("/").endswith("/.overview.md")
            and to_memory_type == "entities"
            and same_namespace
        )

    if from_memory_type == "entities" or to_memory_type == "entities":
        return (
            to_memory_type == "entities"
            and from_context == "memory"
            and _content_namespace(from_uri, from_context) == _content_namespace(to_uri, to_context)
        )

    return True


def _dedup_key(link: Dict[str, Any]) -> str:
    """Compute dedup key for a link."""
    return f"{link.get('from_uri', '')}|{link.get('to_uri', '')}|{link.get('match_text', '')}"


def merge_links(existing_links: List[Dict], new_links: List[Dict]) -> List[Dict]:
    """
    Merge link lists with dedup and conflict resolution.

    Dedup key: from_uri + to_uri + match_text
    Weight conflict: take max
    link_type and description: latest write wins
    """
    link_map: Dict[str, Dict[str, Any]] = {}

    # Process existing links first
    for link in existing_links:
        key = _dedup_key(link)
        link_map[key] = dict(link)

    # Process new links (override existing on conflict)
    for link in new_links:
        key = _dedup_key(link)
        if key in link_map:
            existing = link_map[key]
            # Weight: take max
            existing["weight"] = max(existing.get("weight", 1.0), link.get("weight", 1.0))
            # link_type and description: latest write wins
            if "link_type" in link:
                existing["link_type"] = link["link_type"]
            if "description" in link:
                existing["description"] = link["description"]
            # created_at: keep the original
        else:
            link_map[key] = dict(link)

    return list(link_map.values())
