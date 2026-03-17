# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""BuildingTree container for OpenViking context trees."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from openviking.core.context import Context


class BuildingTree:
    """
    Container for built context tree.

    Maintains:
    - List of all contexts
    - Parent-child relationships
    - Directory structure for listing
    """

    def __init__(
        self,
        source_path: Optional[str] = None,
        source_format: Optional[str] = None,
    ):
        self.source_path = source_path
        self.source_format = source_format
        self._contexts: List["Context"] = []
        self._uri_map: Dict[str, "Context"] = {}
        self._root_uri: Optional[str] = None
        self._candidate_uri: Optional[str] = None

    def add_context(self, context: "Context") -> None:
        """Add a context to the tree."""
        self._contexts.append(context)
        self._uri_map[context.uri] = context

    @property
    def root(self) -> Optional["Context"]:
        """Get root context."""
        if self._root_uri:
            return self._uri_map.get(self._root_uri)
        return None

    @property
    def contexts(self) -> List["Context"]:
        """Get all contexts."""
        return self._contexts

    def get(self, uri: str) -> Optional["Context"]:
        """Get context by URI."""
        return self._uri_map.get(uri)

    def parent(self, uri: str) -> Optional["Context"]:
        """Get parent context of a URI."""
        context = self._uri_map.get(uri)
        if context and context.parent_uri:
            return self._uri_map.get(context.parent_uri)
        return None

    def get_children(self, uri: str) -> List["Context"]:
        """Get children of a URI."""
        return [ctx for ctx in self._contexts if ctx.parent_uri == uri]

    def get_path_to_root(self, uri: str) -> List["Context"]:
        """Get path from context to root."""
        path = []
        current_uri = uri
        while current_uri:
            context = self._uri_map.get(current_uri)
            if not context:
                break
            path.append(context)
            current_uri = context.parent_uri
        return path

    def to_directory_structure(self) -> Dict[str, Any]:
        """Convert tree to directory-like structure."""

        def build_dir(uri: str) -> Dict[str, Any]:
            context = self._uri_map.get(uri)
            if not context:
                return {}
            children = self.get_children(uri)
            # Use semantic_title or source_title from meta
            title = context.meta.get("semantic_title") or context.meta.get(
                "source_title", "Untitled"
            )
            return {
                "uri": uri,
                "title": title,
                "type": context.get_context_type(),
                "children": [build_dir(c.uri) for c in children],
            }

        if self._root_uri:
            return build_dir(self._root_uri)
        return {}

    def __len__(self) -> int:
        return len(self._contexts)

    def __iter__(self):
        return iter(self._contexts)
