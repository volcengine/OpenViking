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

from typing import Dict, Optional

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class PageIdMap:
    """Temporary mapping from page_id to URI for one ExtractLoop run."""

    def __init__(self):
        self._next_id: int = 1
        self._id_to_uri: Dict[int, str] = {}
        self._uri_to_id: Dict[str, int] = {}
        self._existing_namespace_exhausted_warned = False

    def get_page_id(self, uri: str) -> Optional[int]:
        """Register an existing page, returning ``None`` after IDs 1-99 are full.

        Previously registered URIs keep their original ID even after the
        existing-page namespace is exhausted.  Unseen URIs are left entirely
        unregistered so IDs >=100 remain reserved for model-declared new pages.
        """
        if uri in self._uri_to_id:
            return self._uri_to_id[uri]
        if self._next_id >= 100:
            if not self._existing_namespace_exhausted_warned:
                logger.warning(
                    "Existing-page ID namespace 1..99 is full; "
                    "additional read results will omit page_id"
                )
                self._existing_namespace_exhausted_warned = True
            return None
        page_id = self._next_id
        self._next_id += 1
        self._id_to_uri[page_id] = uri
        self._uri_to_id[uri] = page_id
        return page_id

    def register_new_page_id(self, uri: str, page_id: int) -> None:
        self._id_to_uri[page_id] = uri
        if uri not in self._uri_to_id:
            self._uri_to_id[uri] = page_id

    def resolve(self, page_id: int) -> Optional[str]:
        """Resolve page_id to URI."""
        return self._id_to_uri.get(page_id)
