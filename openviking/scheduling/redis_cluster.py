# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis Cluster client that keeps its configured startup nodes."""

from __future__ import annotations

import asyncio
from typing import Any

from redis.asyncio.cluster import ClusterNode, RedisCluster


class SeededRedisCluster(RedisCluster):
    """Start every initialization after a failure from the configured nodes.

    redis-py before 7.2 removes a startup node on each connection error and never
    adds one back, and before 8.0 each failed connect keeps one pool slot of the
    node it tried. Either way the client ends up failing every command without
    reaching Redis, behind a single-node proxy after one error.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._seeds = [(node.host, node.port) for node in self.nodes_manager.startup_nodes.values()]
        self._seed_lock = asyncio.Lock()

    async def initialize(self, *args: Any, **kwargs: Any) -> RedisCluster:
        # A caller cancelled while it waits here must not touch the nodes of the running one.
        async with self._seed_lock:
            startup_nodes = self.nodes_manager.startup_nodes
            if not startup_nodes:
                for host, port in self._seeds:
                    node = ClusterNode(host, port, **self.connection_kwargs)
                    startup_nodes[node.name] = node
            try:
                return await super().initialize(*args, **kwargs)
            except BaseException:
                # redis-py has already disconnected these nodes.
                startup_nodes.clear()
                raise
