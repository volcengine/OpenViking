# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Graph data loader for knowledge-graph-aware retrieval.

Loads relation data from two sources for a batch of URIs:
1. .relations.json (via VikingFS.relations)
2. MEMORY_FIELDS.links/backlinks (via MemoryFileUtils.read)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.retrieve.types import RelatedContext

logger = logging.getLogger(__name__)


@dataclass
class UriGraphData:
    """Graph connectivity data for a single URI."""

    relation_count: int = 0
    link_count: int = 0
    total_count: int = 0
    relations: List[RelatedContext] = field(default_factory=list)


async def load_graph_data_for_uris(
    uris: List[str],
    viking_fs: Any,
    ctx: Any,
    max_relations_per_uri: int = 5,
) -> Dict[str, UriGraphData]:
    """Load graph relation data for a batch of URIs concurrently.

    For each URI, loads from both available graph sources:
    1. .relations.json (via viking_fs.relations())
    2. MEMORY_FIELDS.links/backlinks (via viking_fs.read_file + MemoryFileUtils)

    Returns a dict keyed by URI with connection counts and related context entries.
    """
    tasks = [_load_single_uri(uri, viking_fs, ctx, max_relations_per_uri) for uri in uris]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: Dict[str, UriGraphData] = {}
    for uri, result in zip(uris, results):
        if isinstance(result, BaseException):
            logger.warning(
                "[GraphLoader] Failed to load graph data for %s: %s", uri, result
            )
            output[uri] = UriGraphData()
        else:
            output[uri] = result
    return output


async def _load_single_uri(
    uri: str,
    viking_fs: Any,
    ctx: Any,
    max_relations: int,
) -> UriGraphData:
    """Load graph data for a single URI."""
    relations_list: List[RelatedContext] = []
    relation_count = 0
    link_count = 0

    # Source 1: .relations.json
    try:
        rel_entries = await viking_fs.relations(uri, ctx=ctx)
        if rel_entries:
            relation_count = len(rel_entries)
            for entry in rel_entries:
                rel_uri = entry.get("uri", "")
                reason = entry.get("reason", "")
                if rel_uri:
                    relations_list.append(RelatedContext(uri=rel_uri, abstract=reason))
    except Exception as e:
        logger.debug("[GraphLoader] relations() failed for %s: %s", uri, e)

    # Source 2: MEMORY_FIELDS.links/backlinks (only for .md files)
    if uri.endswith(".md"):
        try:
            raw_content = await viking_fs.read_file(uri, ctx=ctx)
            if raw_content:
                mf = MemoryFileUtils.read(raw_content, uri=uri)
                links = mf.links or []
                backlinks = mf.backlinks or []
                link_count = len(links) + len(backlinks)

                for link in links:
                    target_uri = link.get("to_uri", "") or link.get("uri", "")
                    desc = link.get("description", "")
                    if target_uri:
                        relations_list.append(
                            RelatedContext(uri=target_uri, abstract=desc)
                        )

                for bl in backlinks:
                    source_uri = bl.get("from_uri", "") or bl.get("uri", "")
                    desc = bl.get("description", "")
                    if source_uri:
                        relations_list.append(
                            RelatedContext(uri=source_uri, abstract=desc)
                        )
        except Exception as e:
            logger.debug(
                "[GraphLoader] MEMORY_FIELDS read failed for %s: %s", uri, e
            )

    total = relation_count + link_count
    return UriGraphData(
        relation_count=relation_count,
        link_count=link_count,
        total_count=total,
        relations=relations_list[:max_relations],
    )
