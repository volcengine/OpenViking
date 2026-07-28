"""Pure helpers for resolving temporary Wiki page IDs into stored links."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from openviking.session.memory.dataclass import StoredLink, WikiLink


def pair_link_uris(from_uris: Sequence[str], to_uris: Sequence[str]) -> list[tuple[str, str]]:
    """Prefer pairs in the same memory namespace, then fall back to all non-self pairs."""
    namespace_pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for from_uri in from_uris:
        from_namespace = from_uri.split("/memories/", 1)[0]
        for to_uri in to_uris:
            pair = (from_uri, to_uri)
            if from_uri == to_uri or pair in seen:
                continue
            if from_namespace != to_uri.split("/memories/", 1)[0]:
                continue
            seen.add(pair)
            namespace_pairs.append(pair)
    if namespace_pairs:
        return namespace_pairs

    all_pairs: list[tuple[str, str]] = []
    for from_uri in from_uris:
        for to_uri in to_uris:
            pair = (from_uri, to_uri)
            if from_uri == to_uri or pair in seen:
                continue
            seen.add(pair)
            all_pairs.append(pair)
    return all_pairs


def resolve_wiki_links(
    raw_links: Iterable[WikiLink],
    page_uri_map: Mapping[int, Sequence[str]],
    *,
    strict: bool = False,
) -> list[StoredLink]:
    """Resolve and stably deduplicate WikiLink values using a page-id URI map."""
    resolved: list[StoredLink] = []
    seen: set[tuple[object, ...]] = set()
    now = datetime.now(timezone.utc).isoformat()
    for link in raw_links:
        if link.f is None or link.t is None:
            if strict:
                raise ValueError("WikiLink f and t must be non-null")
            continue
        if link.f == link.t:
            if strict:
                raise ValueError(f"WikiLink cannot reference itself: page_id={link.f}")
            continue
        from_uris = list(dict.fromkeys(page_uri_map.get(link.f, [])))
        to_uris = list(dict.fromkeys(page_uri_map.get(link.t, [])))
        if not from_uris or not to_uris:
            if strict:
                raise ValueError(f"WikiLink references an unknown page_id: f={link.f}, t={link.t}")
            continue
        pairs = pair_link_uris(from_uris, to_uris)
        if not pairs and strict:
            raise ValueError(f"WikiLink resolves only to self-links: f={link.f}, t={link.t}")
        for from_uri, to_uri in pairs:
            key = (
                from_uri,
                to_uri,
                link.link_type,
                link.weight,
                link.match_text,
                link.description,
            )
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                StoredLink(
                    from_uri=from_uri,
                    to_uri=to_uri,
                    link_type=link.link_type,
                    weight=link.weight,
                    match_text=link.match_text,
                    description=link.description,
                    created_at=now,
                )
            )
    return resolved


__all__ = ["pair_link_uris", "resolve_wiki_links"]
