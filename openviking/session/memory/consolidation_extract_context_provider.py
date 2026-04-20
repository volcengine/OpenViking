# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Consolidation Extract Context Provider.

Fills the architectural slot reserved at openviking/session/memory/core.py:8
for ConsolidationExtractContextProvider. This implementation backs ExtractLoop
when consolidation needs ReAct-style exploration of a cluster's local context
(sibling memories, scope overview) before committing merge/delete/archive ops.

In the periodic consolidation pass v1, MemoryConsolidator drives the LLM
directly via MemoryDeduplicator.consolidate_cluster() without ExtractLoop --
that path is sufficient when the cluster fits in one prompt. This provider
exists for the case where a cluster decision needs additional context the
consolidator did not pre-fetch (e.g. parent overview, a sibling memory
referenced by a cluster member). ExtractLoop callers can opt into this
provider for that extended reasoning.
"""

from typing import Any, Dict, List

from openviking.core.context import Context
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.core import ExtractContextProvider
from openviking.session.memory.tools import add_tool_call_pair_to_messages, get_tool
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ConsolidationExtractContextProvider(ExtractContextProvider):
    """ExtractContextProvider for periodic memory consolidation.

    Differs from SessionExtractContextProvider:
    - Input is a fixed cluster of existing memories, not a session transcript.
    - Prefetch surfaces the scope's overview and the cluster's full content,
      so the LLM has everything needed to decide consolidation ops without
      additional tool calls in the common case.
    - Tools are restricted to read (no extraction tools, no writes through
      this loop -- writes are applied by MemoryConsolidator after parsing
      the LLM decision).
    - get_memory_schemas returns an empty list because consolidation does
      not extract new candidate memories; it only reorganizes existing ones.
    """

    def __init__(
        self,
        cluster: List[Context],
        scope_uri: str,
        scope_overview: str = "",
        cluster_contents: Dict[str, str] | None = None,
    ):
        """Build the provider for one cluster decision.

        Args:
            cluster: Existing memories that form the cluster.
            scope_uri: URI of the consolidation scope (for prompt context).
            scope_overview: Pre-fetched scope .overview.md text or "".
            cluster_contents: Pre-fetched uri -> body map. Members not in
                the map are sent as abstract only.
        """
        self._cluster = cluster
        self._scope_uri = scope_uri
        self._scope_overview = scope_overview or "(none)"
        self._cluster_contents = cluster_contents or {}

    def instruction(self) -> str:
        return (
            "You are consolidating a cluster of similar existing memories. "
            "All cluster members are already stored. Decide the cluster outcome:\n"
            "- keep_and_merge: same subject; pick a keeper and fold others in\n"
            "- keep_and_delete: one member fully invalidates others\n"
            "- archive_all: cluster is stale; move all to _archive\n"
            "- keep_all: members are not actually duplicates (false positive)\n\n"
            "Convert relative dates to absolute dates in any merged content. "
            "Prefer non-destructive choices when uncertain (keep_all over delete). "
            "Output JSON only -- see the cluster_consolidate template for the schema."
        )

    async def prefetch(
        self,
        ctx: RequestContext,
        viking_fs: VikingFS,
        transaction_handle,
        vlm,
    ) -> List[Dict]:
        """Surface the scope overview as a tool_call message.

        Cluster members and their contents are passed in via __init__ and
        rendered into the prompt by the caller, not via tool calls -- the
        LLM should not need to discover them. This prefetch only surfaces
        scope-level context the LLM may want to reason about (the overview).
        """
        pre_fetch_messages: List[Dict] = []
        read_tool = get_tool("read")
        if not read_tool or not viking_fs:
            return pre_fetch_messages

        tool_ctx = ToolContext(
            request_ctx=ctx,
            transaction_handle=transaction_handle,
            default_search_uris=[],
        )
        overview_uri = self._scope_uri.rstrip("/") + "/.overview.md"
        try:
            result_str = await read_tool.execute(viking_fs, tool_ctx, uri=overview_uri)
            add_tool_call_pair_to_messages(
                messages=pre_fetch_messages,
                call_id=0,
                tool_name="read",
                params={"uri": overview_uri},
                result=result_str,
            )
        except Exception as e:
            logger.debug(f"Scope overview not available at {overview_uri}: {e}")

        return pre_fetch_messages

    def get_tools(self) -> List[str]:
        """Read-only tool surface.

        The cluster decision is encoded in the LLM's JSON output and
        applied by MemoryConsolidator. Write/edit/delete tools are
        intentionally excluded so the LLM cannot mutate state directly.
        """
        return ["read"]

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """Consolidation does not extract new memories; return [].

        Returning an empty list signals to ExtractLoop that no
        per-memory-type schema rendering is needed. Cluster category
        is implicit in the cluster members.
        """
        return []
