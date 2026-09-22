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

from typing import Dict, Iterable, Optional

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ResponsePageIdAllocator:
    """Allocate unique new-page IDs for one candidate LLM response."""

    def __init__(self, registered_page_ids: Iterable[int] = ()):
        self._registered_page_ids = set(registered_page_ids)
        self._allocated_page_ids: set[int] = set()
        self._next_page_id = 100

    def allocate(self, requested_page_id: Optional[int]) -> int:
        if (
            requested_page_id is not None
            and requested_page_id >= 100
            and requested_page_id not in self._registered_page_ids
            and requested_page_id not in self._allocated_page_ids
        ):
            page_id = requested_page_id
        else:
            page_id = self._next_page_id
            while page_id in self._registered_page_ids or page_id in self._allocated_page_ids:
                page_id += 1

        self._allocated_page_ids.add(page_id)
        while (
            self._next_page_id in self._registered_page_ids
            or self._next_page_id in self._allocated_page_ids
        ):
            self._next_page_id += 1
        return page_id


class PageIdMap:
    """Temporary mapping from page_id to URI for one ExtractLoop run."""

    def __init__(self):
        self._next_id: int = 1
        self._id_to_uri: Dict[int, str] = {}
        self._uri_to_id: Dict[str, int] = {}

    def get_page_id(self, uri: str) -> int:
        """Register an existing page (from prefetch/read). Returns page_id in 1-99 range."""
        if uri in self._uri_to_id:
            return self._uri_to_id[uri]
        page_id = self._next_id
        self._next_id += 1
        self._id_to_uri[page_id] = uri
        self._uri_to_id[uri] = page_id
        return page_id

    def register_new_page_id(self, uri: str, page_id: int) -> None:
        self._id_to_uri[page_id] = uri
        if uri not in self._uri_to_id:
            self._uri_to_id[uri] = page_id

    def new_page_id_allocator(self) -> ResponsePageIdAllocator:
        return ResponsePageIdAllocator(self._id_to_uri)

    def resolve(self, page_id: int) -> Optional[str]:
        """Resolve page_id to URI."""
        return self._id_to_uri.get(page_id)
