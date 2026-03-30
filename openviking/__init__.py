# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking - A Context Database for AI Agents.

OpenViking is an open-source context database designed specifically for AI Agents.
It provides a filesystem paradigm for unified context management, enabling developers
to build an Agent's brain just like managing local files.

Key Features:
    - Filesystem Management Paradigm: Unified context management of memories,
      resources, and skills based on a filesystem paradigm.
    - Tiered Context Loading: L0/L1/L2 three-tier structure, loaded on demand.
    - Directory Recursive Retrieval: Native filesystem retrieval methods with
      directory positioning and semantic search.
    - Visualized Retrieval Trajectory: Supports visualization of directory
      retrieval trajectories.
    - Automatic Session Management: Automatically compresses content and extracts
      long-term memory.

Basic Usage:
    >>> import openviking
    >>>
    >>> # Initialize client (embedded mode)
    >>> client = openviking.SyncOpenViking()
    >>> client.initialize()
    >>>
    >>> # Add a resource
    >>> client.add_resource("https://github.com/example/repo", wait=True)
    >>>
    >>> # Search for context
    >>> results = client.find("what is openviking")
    >>>
    >>> # Create a session for conversation
    >>> session = client.session()
    >>> client.add_message(session.session_id, "user", "Hello!")
    >>> client.commit_session(session.session_id)
    >>>
    >>> # Clean up
    >>> client.close()

For async usage:
    >>> from openviking import AsyncOpenViking
    >>>
    >>> async def main():
    ...     client = AsyncOpenViking()
    ...     await client.initialize()
    ...     # ... use client ...
    ...     await client.close()

For HTTP mode (connecting to a remote server):
    >>> from openviking import AsyncHTTPClient, SyncHTTPClient
    >>>
    >>> # Async HTTP client
    >>> client = AsyncHTTPClient(url="http://localhost:1933")
    >>>
    >>> # Sync HTTP client
    >>> client = SyncHTTPClient(url="http://localhost:1933")

Modules:
    - openviking.client: Main client classes (SyncOpenViking, AsyncOpenViking)
    - openviking.session: Session management for conversations
    - openviking.storage: Storage backends and vector databases
    - openviking.retrieve: Retrieval and search functionality
    - openviking.models: Data models and schemas

See Also:
    - GitHub: https://github.com/volcengine/OpenViking
    - Documentation: https://www.openviking.ai/docs
    - Discord: https://discord.com/invite/eHvx8E9XF3
"""

try:
    from ._version import version as __version__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("openviking")
    except ImportError:
        __version__ = "0.0.0+unknown"

try:
    from openviking.pyagfs import AGFSClient
except ImportError as exc:
    raise ImportError(
        "Bundled OpenViking AGFS client is unavailable. "
        "Reinstall openviking or run 'pip install -e .' from the project root."
    ) from exc


def __getattr__(name: str):
    if name == "AsyncOpenViking":
        from openviking.async_client import AsyncOpenViking

        return AsyncOpenViking
    if name == "SyncOpenViking":
        from openviking.sync_client import SyncOpenViking

        return SyncOpenViking
    if name == "OpenViking":
        from openviking.sync_client import SyncOpenViking

        return SyncOpenViking
    if name == "Session":
        from openviking.session import Session

        return Session
    if name == "AsyncHTTPClient":
        from openviking_cli.client.http import AsyncHTTPClient

        return AsyncHTTPClient
    if name == "SyncHTTPClient":
        from openviking_cli.client.sync_http import SyncHTTPClient

        return SyncHTTPClient
    if name == "UserIdentifier":
        from openviking_cli.session.user_id import UserIdentifier

        return UserIdentifier
    raise AttributeError(name)


__all__ = [
    "OpenViking",
    "SyncOpenViking",
    "AsyncOpenViking",
    "SyncHTTPClient",
    "AsyncHTTPClient",
    "Session",
    "UserIdentifier",
]
