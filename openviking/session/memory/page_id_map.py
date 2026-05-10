# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PageIdMap - Temporary page_id to URI mapping for one ExtractLoop lifecycle.

Existing pages (from prefetch/read): IDs 1-99
New pages (from LLM output): IDs 100+

A URI can have multiple page_ids pointing to it (e.g. existing page_id=1 from
prefetch, plus LLM-declared page_id=100 when editing the same page).
Both IDs resolve to the same URI.

page_id information is injected into LLM context by annotating read results
with [page_id: N], not by generating a separate mapping table.
"""

from typing import Dict, List, Optional, Set

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class PageIdMap:
    """Temporary mapping from page_id to URI for one ExtractLoop run."""

    MAX_EXISTING_ID = 99

    def __init__(self):
        self._next_id: int = 1
        self._id_to_uri: Dict[int, str] = {}
        # A URI can map to multiple page_ids (existing + LLM-declared aliases)
        self._uri_to_ids: Dict[str, Set[int]] = {}

    def register_existing(self, uri: str) -> int:
        """Register an existing page (from prefetch/read). Returns page_id in 1-99 range."""
        if uri in self._uri_to_ids:
            # Return the smallest (original) page_id for this URI
            return min(self._uri_to_ids[uri])
        if self._next_id > self.MAX_EXISTING_ID:
            raise ValueError(f"Too many existing pages for PageIdMap (max {self.MAX_EXISTING_ID})")
        page_id = self._next_id
        self._next_id += 1
        self._id_to_uri[page_id] = uri
        self._uri_to_ids[uri] = {page_id}
        return page_id

    def register_new(self, uri: str, page_id: Optional[int] = None) -> int:
        """Register a new page (from LLM output). Returns page_id >= 100.

        Args:
            uri: The URI of the new page.
            page_id: The page_id declared by the LLM (must be >= 100).
                     If None, auto-assigns the next available ID.
        """
        # Even if URI already exists, still register the LLM-declared page_id
        # as an alias so links using it can resolve correctly.
        if page_id is not None and page_id >= 100:
            if page_id not in self._id_to_uri:
                self._id_to_uri[page_id] = uri
                self._uri_to_ids.setdefault(uri, set()).add(page_id)
                logger.debug(f"PageIdMap: registered LLM page_id={page_id} -> {uri}")
                return page_id
            else:
                # Collision: LLM declared same page_id for different URIs
                existing_uri = self._id_to_uri[page_id]
                if existing_uri == uri:
                    # Same URI, same page_id - already registered
                    return page_id
                # Different URI claims same page_id - auto-assign
                page_id = self._next_available_new_id()
                self._id_to_uri[page_id] = uri
                self._uri_to_ids.setdefault(uri, set()).add(page_id)
                logger.warning(f"PageIdMap: page_id collision, auto-assigned {page_id} -> {uri}")
                return page_id

        # Auto-assign (no LLM-declared page_id)
        if uri in self._uri_to_ids:
            return min(self._uri_to_ids[uri])
        page_id = self._next_available_new_id()
        self._id_to_uri[page_id] = uri
        self._uri_to_ids.setdefault(uri, set()).add(page_id)
        return page_id

    def _next_available_new_id(self) -> int:
        """Find the next available page_id >= 100."""
        candidate = 100
        while candidate in self._id_to_uri:
            candidate += 1
        return candidate

    def register_alias(self, uri: str, page_id: int) -> None:
        """Register a URI as an alias for an existing page_id.

        Used when a single operation produces multiple URIs (multi-user mode).
        The alias URI maps back to the same page_id, so resolve(page_id)
        returns the primary URI. get_id(alias_uri) returns the page_id.
        """
        if uri in self._uri_to_ids:
            return  # URI already registered
        self._uri_to_ids[uri] = {page_id}
        logger.debug(f"PageIdMap: registered alias {uri} -> page_id={page_id}")

    def resolve(self, page_id: int) -> Optional[str]:
        """Resolve page_id to URI."""
        return self._id_to_uri.get(page_id)

    def get_id(self, uri: str) -> Optional[int]:
        """Get the primary page_id for a URI."""
        if uri in self._uri_to_ids:
            return min(self._uri_to_ids[uri])
        return None

    @property
    def has_links_enabled(self) -> bool:
        """Whether any pages have been registered (links feature is active)."""
        return len(self._id_to_uri) > 0
