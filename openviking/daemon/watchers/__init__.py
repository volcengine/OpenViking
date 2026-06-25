# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Watcher abstractions for OpenViking Active Daemon.
Provides BaseWatcher protocol and watcher registry for multi-tool support.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class BaseWatcher(Protocol):
    """Protocol that all tool-specific watchers must implement."""

    @property
    def tool_name(self) -> str:
        """Return the identifier for this watcher's tool (e.g. 'claude_code', 'aider')."""
        ...

    def start(self) -> None:
        """Start watching for file/database changes."""
        ...

    def stop(self) -> None:
        """Stop watching and release resources."""
        ...

    def flush(self) -> None:
        """Force flush any buffered events."""
        ...
