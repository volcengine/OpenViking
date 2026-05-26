# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only memory graph health inspection helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import LINK_TYPE_DEFAULT
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

_SKIP_MEMORY_FILENAMES = {".overview.md", ".abstract.md", ".graph.html"}


def _is_memory_markdown(entry: dict[str, Any]) -> bool:
    if entry.get("isDir"):
        return False
    uri = str(entry.get("uri") or "")
    rel_path = str(entry.get("rel_path") or uri.rsplit("/", 1)[-1])
    filename = rel_path.rsplit("/", 1)[-1]
    return (
        filename.endswith(".md")
        and filename not in _SKIP_MEMORY_FILENAMES
        and not filename.startswith(".")
    )


def _infer_memory_type(uri: str, parsed_memory_type: str | None) -> str:
    if parsed_memory_type:
        return str(parsed_memory_type)
    marker = "/memories/"
    if marker in uri:
        tail = uri.split(marker, 1)[1].strip("/")
        parts = [part for part in tail.split("/") if part]
        if len(parts) >= 2:
            return parts[0]
    return "unknown"


def _link_key(raw_link: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(raw_link.get("from_uri") or ""),
        str(raw_link.get("to_uri") or ""),
        str(raw_link.get("link_type") or LINK_TYPE_DEFAULT),
    )


def _append_sample(
    samples: list[dict[str, Any]],
    *,
    sample_limit: int,
    kind: str,
    uri: str,
    peer_uri: str = "",
    link_type: str = "",
    detail: str = "",
) -> None:
    if sample_limit <= 0 or len(samples) >= sample_limit:
        return
    sample = {"kind": kind, "uri": uri}
    if peer_uri:
        sample["peer_uri"] = peer_uri
    if link_type:
        sample["link_type"] = link_type
    if detail:
        sample["detail"] = detail
    samples.append(sample)


async def inspect_memory_graph_health(
    viking_fs: Any,
    root_uri: str,
    *,
    ctx: RequestContext,
    node_limit: int = 5000,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """Scan one memory root and summarize link/backlink consistency.

    The helper is intentionally read-only and storage-backed. It is meant for
    corpus-build gates and diagnostics after concurrent memory writes, not for
    hot-path request metrics.
    """
    entries = await viking_fs.tree(
        root_uri,
        output="original",
        show_all_hidden=False,
        node_limit=node_limit,
        level_limit=None,
        ctx=ctx,
    )
    md_uris = [str(entry.get("uri")) for entry in entries if _is_memory_markdown(entry)]

    nodes: dict[str, Any] = {}
    memory_type_by_uri: dict[str, str] = {}
    samples: list[dict[str, Any]] = []
    parse_error_count = 0

    for uri in md_uris:
        try:
            raw_content = await viking_fs.read_file(uri, ctx=ctx)
            if raw_content is None:
                raise ValueError("empty read")
            mf = MemoryFileUtils.read(raw_content, uri=uri)
        except Exception as exc:
            parse_error_count += 1
            _append_sample(
                samples,
                sample_limit=sample_limit,
                kind="parse_error",
                uri=uri,
                detail=type(exc).__name__,
            )
            continue
        nodes[uri] = mf
        memory_type_by_uri[uri] = _infer_memory_type(uri, mf.memory_type)

    forward_edges: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    backlink_edges: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    malformed_link_count = 0
    owner_mismatch_count = 0
    duplicate_link_count = 0
    seen_forward_by_owner: set[tuple[str, str, str, str]] = set()
    seen_backlink_by_owner: set[tuple[str, str, str, str]] = set()

    for owner_uri, mf in nodes.items():
        for raw_link in mf.links or []:
            if not isinstance(raw_link, dict):
                malformed_link_count += 1
                _append_sample(
                    samples,
                    sample_limit=sample_limit,
                    kind="malformed_link",
                    uri=owner_uri,
                    detail="forward link is not an object",
                )
                continue
            from_uri, to_uri, link_type = _link_key(raw_link)
            if not from_uri or not to_uri:
                malformed_link_count += 1
                _append_sample(
                    samples,
                    sample_limit=sample_limit,
                    kind="malformed_link",
                    uri=owner_uri,
                    detail="missing from_uri or to_uri",
                )
                continue
            if from_uri != owner_uri:
                owner_mismatch_count += 1
                _append_sample(
                    samples,
                    sample_limit=sample_limit,
                    kind="forward_owner_mismatch",
                    uri=owner_uri,
                    peer_uri=to_uri,
                    link_type=link_type,
                    detail=f"from_uri={from_uri}",
                )
            owner_key = (owner_uri, from_uri, to_uri, link_type)
            if owner_key in seen_forward_by_owner:
                duplicate_link_count += 1
            seen_forward_by_owner.add(owner_key)
            forward_edges[(from_uri, to_uri, link_type)].add(owner_uri)

        for raw_link in mf.backlinks or []:
            if not isinstance(raw_link, dict):
                malformed_link_count += 1
                _append_sample(
                    samples,
                    sample_limit=sample_limit,
                    kind="malformed_backlink",
                    uri=owner_uri,
                    detail="backlink is not an object",
                )
                continue
            from_uri, to_uri, link_type = _link_key(raw_link)
            if not from_uri or not to_uri:
                malformed_link_count += 1
                _append_sample(
                    samples,
                    sample_limit=sample_limit,
                    kind="malformed_backlink",
                    uri=owner_uri,
                    detail="missing from_uri or to_uri",
                )
                continue
            if to_uri != owner_uri:
                owner_mismatch_count += 1
                _append_sample(
                    samples,
                    sample_limit=sample_limit,
                    kind="backlink_owner_mismatch",
                    uri=owner_uri,
                    peer_uri=from_uri,
                    link_type=link_type,
                    detail=f"to_uri={to_uri}",
                )
            owner_key = (owner_uri, from_uri, to_uri, link_type)
            if owner_key in seen_backlink_by_owner:
                duplicate_link_count += 1
            seen_backlink_by_owner.add(owner_key)
            backlink_edges[(from_uri, to_uri, link_type)].add(owner_uri)

    broken_endpoint_count = 0
    missing_backlink_count = 0
    missing_forward_link_count = 0

    for (from_uri, to_uri, link_type), owner_uris in forward_edges.items():
        if to_uri not in nodes:
            broken_endpoint_count += len(owner_uris)
            _append_sample(
                samples,
                sample_limit=sample_limit,
                kind="broken_forward_endpoint",
                uri=from_uri,
                peer_uri=to_uri,
                link_type=link_type,
            )
            continue
        if to_uri not in backlink_edges.get((from_uri, to_uri, link_type), set()):
            missing_backlink_count += len(owner_uris)
            _append_sample(
                samples,
                sample_limit=sample_limit,
                kind="missing_backlink",
                uri=from_uri,
                peer_uri=to_uri,
                link_type=link_type,
            )

    for (from_uri, to_uri, link_type), owner_uris in backlink_edges.items():
        if from_uri not in nodes:
            broken_endpoint_count += len(owner_uris)
            _append_sample(
                samples,
                sample_limit=sample_limit,
                kind="broken_backlink_endpoint",
                uri=to_uri,
                peer_uri=from_uri,
                link_type=link_type,
            )
            continue
        if from_uri not in forward_edges.get((from_uri, to_uri, link_type), set()):
            missing_forward_link_count += len(owner_uris)
            _append_sample(
                samples,
                sample_limit=sample_limit,
                kind="missing_forward_link",
                uri=to_uri,
                peer_uri=from_uri,
                link_type=link_type,
            )

    memory_type_counts = Counter(memory_type_by_uri.values())
    experience_uris = [
        uri for uri, memory_type in memory_type_by_uri.items() if memory_type == "experiences"
    ]
    trajectory_uris = {
        uri for uri, memory_type in memory_type_by_uri.items() if memory_type == "trajectories"
    }
    experience_to_trajectory_links = sum(
        1
        for from_uri, to_uri, _link_type in forward_edges
        if from_uri in experience_uris and to_uri in trajectory_uris
    )
    trajectory_from_experience_backlinks = sum(
        1
        for from_uri, to_uri, _link_type in backlink_edges
        if from_uri in experience_uris and to_uri in trajectory_uris
    )
    source_linkless_experience_uris = [
        uri
        for uri in experience_uris
        if not any(
            from_uri == uri and to_uri in trajectory_uris for from_uri, to_uri, _ in forward_edges
        )
    ]

    violation_count = (
        parse_error_count
        + malformed_link_count
        + owner_mismatch_count
        + broken_endpoint_count
        + missing_backlink_count
        + missing_forward_link_count
    )

    return {
        "root_uri": root_uri,
        "scanned_entry_count": len(entries),
        "memory_file_count": len(nodes),
        "memory_type_counts": dict(sorted(memory_type_counts.items())),
        "forward_link_count": sum(len(owners) for owners in forward_edges.values()),
        "backlink_count": sum(len(owners) for owners in backlink_edges.values()),
        "experience_to_trajectory_links": experience_to_trajectory_links,
        "trajectory_from_experience_backlinks": trajectory_from_experience_backlinks,
        "source_linkless_experience_count": len(source_linkless_experience_uris),
        "parse_error_count": parse_error_count,
        "malformed_link_count": malformed_link_count,
        "owner_mismatch_count": owner_mismatch_count,
        "duplicate_link_count": duplicate_link_count,
        "broken_endpoint_count": broken_endpoint_count,
        "missing_backlink_count": missing_backlink_count,
        "missing_forward_link_count": missing_forward_link_count,
        "healthy": violation_count == 0 and not source_linkless_experience_uris,
        "samples": samples,
    }
