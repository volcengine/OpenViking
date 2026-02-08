# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""DAGCoordinator: Manages dependency graph for semantic processing."""

import threading
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from openviking.utils import VikingURI
from openviking.utils.logger import get_logger

logger = get_logger(__name__)


class DAGCoordinator:
    """
    Manages the dependency graph for tasks.
    Tracks parent-child relationships and triggers parent tasks when all children are complete.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Map parent URI -> Number of pending children
        # This acts as the "in-degree" counter for the parent in the dependency graph
        self._pending_count: Dict[str, int] = defaultdict(int)

        # Map URI -> Context Type (needed to enqueue the task)
        self._context_types: Dict[str, str] = {}

        # Track active tasks to prevent duplicate processing
        self._active_tasks: Set[str] = set()

    def register_node(self, uri: str, context_type: str):
        """Register a node in the graph."""
        with self._lock:
            self._context_types[uri] = context_type
            # Initialize count if not exists
            if uri not in self._pending_count:
                self._pending_count[uri] = 0

    def register_dependency(self, parent_uri: str, child_uri: str):
        """
        Register a dependency: parent depends on child.
        Must be called after register_node for both parent and child.
        """
        with self._lock:
            self._pending_count[parent_uri] += 1

    def get_leaves(self) -> List[Tuple[str, str]]:
        """
        Get all tasks that have no pending dependencies (count == 0).
        Returns list of (uri, context_type).
        """
        initial_tasks = []
        with self._lock:
            for uri, count in self._pending_count.items():
                if count == 0:
                    if uri not in self._active_tasks:
                        initial_tasks.append((uri, self._context_types[uri]))
                        self._active_tasks.add(uri)
        return initial_tasks

    def complete_task(self, uri: str) -> Optional[Tuple[str, str]]:
        """
        Mark a task as complete.
        Propagates completion to parent.
        Returns (parent_uri, context_type) if a parent becomes ready, else None.
        """
        ready_parent = None
        with self._lock:
            # 1. Derive parent URI
            parent_obj = VikingURI(uri).parent
            if not parent_obj:
                return None
            parent_uri = parent_obj.uri

            # 2. Check if parent is tracked in our graph
            if parent_uri in self._pending_count:
                # 3. Decrement pending count
                self._pending_count[parent_uri] -= 1

                # 4. Check if parent is now ready
                if self._pending_count[parent_uri] <= 0:
                    if parent_uri not in self._active_tasks:
                        context_type = self._context_types.get(parent_uri, "resource")
                        ready_parent = (parent_uri, context_type)
                        self._active_tasks.add(parent_uri)

        return ready_parent

    def reset(self):
        """Reset the coordinator state."""
        with self._lock:
            self._pending_count.clear()
            self._context_types.clear()
            self._active_tasks.clear()
