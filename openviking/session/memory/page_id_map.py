# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PageIdMap - Temporary page_id to URI mapping for one ExtractLoop lifecycle.

Existing pages (from prefetch/read): IDs 1-99
New pages (from LLM output): IDs 100+

page_id information is injected into LLM context by annotating read results
with [page_id: N], not by generating a separate mapping table.
"""

from typing import Dict, Optional


class PageIdMap:
    """Temporary mapping from page_id to URI for one ExtractLoop run."""

    MAX_EXISTING_ID = 99

    def __init__(self):
        self._next_id: int = 1
        self._id_to_uri: Dict[int, str] = {}
        self._uri_to_id: Dict[str, int] = {}

    def register_existing(self, uri: str) -> int:
        """Register an existing page (from prefetch/read). Returns page_id in 1-99 range."""
        if uri in self._uri_to_id:
            return self._uri_to_id[uri]
        if self._next_id > self.MAX_EXISTING_ID:
            raise ValueError(f"Too many existing pages for PageIdMap (max {self.MAX_EXISTING_ID})")
        page_id = self._next_id
        self._next_id += 1
        self._id_to_uri[page_id] = uri
        self._uri_to_id[uri] = page_id
        return page_id

    def register_new(self, uri: str, page_id: Optional[int] = None) -> int:
        """Register a new page (from LLM output). Returns page_id >= 100.

        Args:
            uri: The URI of the new page.
            page_id: The page_id declared by the LLM (must be >= 100).
                     If None, auto-assigns the next available ID.
        """
        if uri in self._uri_to_id:
            return self._uri_to_id[uri]
        if page_id is not None and page_id >= 100:
            # Use the LLM-declared page_id
            if page_id in self._id_to_uri:
                # Collision: LLM declared same page_id for different URIs, auto-assign
                page_id = self._next_available_new_id()
            self._id_to_uri[page_id] = uri
            self._uri_to_id[uri] = page_id
            return page_id
        # Auto-assign
        page_id = self._next_available_new_id()
        self._id_to_uri[page_id] = uri
        self._uri_to_id[uri] = page_id
        return page_id

    def _next_available_new_id(self) -> int:
        """Find the next available page_id >= 100."""
        candidate = 100
        while candidate in self._id_to_uri:
            candidate += 1
        return candidate

    def resolve(self, page_id: int) -> Optional[str]:
        """Resolve page_id to URI."""
        return self._id_to_uri.get(page_id)

    def get_id(self, uri: str) -> Optional[int]:
        """Get page_id for a URI."""
        return self._uri_to_id.get(uri)

    @property
    def has_links_enabled(self) -> bool:
        """Whether any pages have been registered (links feature is active)."""
        return len(self._id_to_uri) > 0
