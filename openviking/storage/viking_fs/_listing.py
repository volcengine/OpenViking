# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Window-aware listing primitives for VikingFS.

A listing window (``since`` / ``until``) is a predicate on filesystem entry
metadata. It is tightly coupled with the listing order, the offset/limit
handling, the directory-before-file grouping and ``has_more``, so the
contract lives in the filesystem layer: every caller - the REST route, the
MCP tool, the SDKs and internal callers - shares the same pagination
semantics instead of compensating for the window in its own layer.

The native listing does not expose a modification-time predicate, so the
window is applied here on top of the raw pages. A future pushdown into the
native listing slots in below this contract without changing any caller.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.utils.search_filters import (
    filter_entries_by_time,
    parse_timestamp_value,
    resolve_time_bounds,
)


@dataclass
class ListingPage:
    """A page of listing entries and whether more entries are available."""

    entries: List[Any]
    has_more: bool = False


#: Filters one batch of listing entries; the caller decides what to keep.
EntryBatchFilter = Callable[[List[Dict[str, Any]]], Awaitable[List[Dict[str, Any]]]]


class _ListingMixin:
    """Listing entry points that own the window and the pagination contract."""

    @staticmethod
    def _time_window_exhausted(
        entries: List[Dict[str, Any]],
        since: Optional[datetime],
        until: Optional[datetime],
        sort_by: Optional[str],
        sort_order: str,
    ) -> bool:
        """Whether a time-sorted listing has already moved past the window.

        Only mtime-ordered listings can be cut short, and only on the side the
        listing walks away from: a descending listing ends with the oldest
        entries, so it is exhausted once the last entry is older than the
        lower bound; an ascending listing ends with the newest entries, so it
        is exhausted once the last entry is newer than the upper bound. Name
        ordered listings have to be scanned to the end.

        A listing is ordered as "directories first, then files", each group
        sorted by modification time. The tail of a batch is therefore the
        oldest *file* only once the scan has moved past the directory group;
        before that the next batch can still hold directories, and further
        down the listing files, that belong inside the window. Stopping there
        would drop entries that are inside the window.
        """
        if sort_by != "mtime" or not entries:
            return False
        if not any(not entry.get("isDir") for entry in entries):
            return False
        timestamp = parse_timestamp_value(entries[-1].get("modTime"))
        if timestamp is None:
            return False
        if sort_order == "desc" and since is not None:
            return timestamp < since
        if sort_order == "asc" and until is not None:
            return timestamp > until
        return False

    async def list_page(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
        *,
        recursive: bool = False,
        directories_only: bool = False,
        level_limit: int = 3,
        show_all_hidden: bool = False,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        since: Optional[str] = None,
        until: Optional[str] = None,
        offset: int = 0,
        node_limit: int = 1000,
        entry_filter: Optional[EntryBatchFilter] = None,
    ) -> ListingPage:
        """List entries, optionally restricted to a modification-time window.

        The window is a predicate on the modification time of each entry and
        is evaluated before ``offset`` / ``node_limit``, so a narrow window
        still returns the entries that sit past the first raw page instead of
        coming back empty.

        Args:
            uri: Viking URI of the directory to list.
            ctx: Request identity used for the access check.
            recursive: Whether to walk the subtree instead of one directory.
            directories_only: Whether to keep only the directories (tree).
            level_limit: Maximum depth of the subtree walk (tree).
            show_all_hidden: Whether to include hidden entries.
            sort_by: Sort field of the underlying listing.
            sort_order: Sort direction of the underlying listing.
            since: Inclusive lower bound on the entry modification time.
            until: Inclusive upper bound on the entry modification time.
            offset: Number of matched entries to skip.
            node_limit: Maximum number of matched entries to return.
            entry_filter: Optional filter applied to every matched batch.

        Returns:
            The requested page and whether more matched entries are available.

        Raises:
            ValueError: If the window is inverted or the sort options are
                invalid.
        """
        if sort_by not in {None, "name", "mtime"}:
            raise ValueError("sort_by must be 'name' or 'mtime'")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be 'asc' or 'desc'")
        since_dt, until_dt = resolve_time_bounds(since=since, until=until)
        has_window = since_dt is not None or until_dt is not None

        ctx = self.authorize_read(self._ctx_or_default(ctx), [uri])
        await self._ensure_traversable(uri, ctx)

        async def fetch_page(page_offset: int, page_limit: Optional[int]) -> List[Dict[str, Any]]:
            """Load one raw page of the underlying listing."""
            if recursive:
                return await self._tree_original(
                    uri,
                    show_all_hidden=show_all_hidden,
                    directories_only=directories_only,
                    node_limit=page_limit,
                    level_limit=level_limit,
                    offset=page_offset,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    ctx=ctx,
                )
            return await self._ls_original(
                uri,
                show_all_hidden=show_all_hidden,
                node_limit=page_limit,
                offset=page_offset,
                sort_by=sort_by,
                sort_order=sort_order,
                ctx=ctx,
            )

        async def matched_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """Keep the entries inside the window and inside the caller filter."""
            if has_window:
                entries = filter_entries_by_time(entries, since_dt, until_dt)
            if entry_filter is not None:
                entries = await entry_filter(entries)
            return entries

        if not has_window and entry_filter is None:
            # Nothing has to be filtered out, so one raw page is enough.
            fetch_limit = node_limit + 1 if node_limit > 0 else node_limit
            entries = await fetch_page(offset, fetch_limit)
            return ListingPage(
                entries=entries[:node_limit] if node_limit > 0 else entries,
                has_more=node_limit > 0 and len(entries) > node_limit,
            )

        if node_limit <= 0:
            # A filtered listing without a limit returns every match.
            entries = await matched_entries(await fetch_page(0, None))
            return ListingPage(entries=entries[offset:], has_more=False)

        batch_size = max(node_limit + 1, 256)
        probe_limit = node_limit + 1
        source_offset = 0
        remaining_offset = offset
        result: List[Dict[str, Any]] = []
        while len(result) < probe_limit:
            entries = await fetch_page(source_offset, batch_size)
            filtered = await matched_entries(entries)
            if remaining_offset >= len(filtered):
                remaining_offset -= len(filtered)
            else:
                result.extend(
                    filtered[remaining_offset : remaining_offset + probe_limit - len(result)]
                )
                remaining_offset = 0
            if len(entries) < batch_size:
                break
            if self._time_window_exhausted(entries, since_dt, until_dt, sort_by, sort_order):
                break
            source_offset += len(entries)
        return ListingPage(entries=result[:node_limit], has_more=len(result) > node_limit)
