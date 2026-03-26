# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Synchronous OpenViking client implementation.

This module provides the synchronous client for OpenViking, which wraps the
asynchronous AsyncOpenViking client with synchronous methods. This is useful
for applications that don't use asyncio or for simpler scripting scenarios.

The SyncOpenViking class provides the same functionality as AsyncOpenViking
but with blocking method calls. For high-performance applications or those
already using asyncio, AsyncOpenViking is recommended.

Example:
    >>> from openviking import SyncOpenViking
    >>> 
    >>> # Create and initialize client
    >>> client = SyncOpenViking()
    >>> client.initialize()
    >>> 
    >>> # Add a resource
    >>> result = client.add_resource(
    ...     "https://github.com/example/repo",
    ...     wait=True
    ... )
    >>> 
    >>> # Search for context
    >>> results = client.find("what is openviking")
    >>> 
    >>> # Create a session
    >>> session = client.session()
    >>> client.add_message(session.session_id, "user", "Hello!")
    >>> client.commit_session(session.session_id)
    >>> 
    >>> # Clean up
    >>> client.close()

Note:
    For HTTP mode (connecting to a remote OpenViking server), use
    SyncHTTPClient from openviking_cli.client instead.

See Also:
    - AsyncOpenViking: Asynchronous version of this client
    - SyncHTTPClient: HTTP client for remote server connection
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from openviking.session import Session

from openviking.async_client import AsyncOpenViking
from openviking.telemetry import TelemetryRequest
from openviking_cli.utils import run_async


class SyncOpenViking:
    """
    Synchronous OpenViking client for embedded mode.

    This is the synchronous version of the OpenViking client, providing
    blocking access to all OpenViking features. It wraps AsyncOpenViking
    internally and handles the async event loop for you.

    Key Features:
        - Resource Management: Add files, URLs, and repositories as resources
        - Semantic Search: Find relevant context using natural language queries
        - Session Management: Track conversations and extract memories
        - Filesystem Operations: Navigate and manage context like a filesystem

    Note:
        This client runs in embedded mode using local storage. For connecting
        to a remote OpenViking server, use SyncHTTPClient from
        openviking_cli.client instead.

    For applications using asyncio, AsyncOpenViking is recommended for
    better performance.

    Examples:
        Basic usage:
        
        >>> from openviking import SyncOpenViking
        >>> 
        >>> client = SyncOpenViking()
        >>> client.initialize()
        >>> 
        >>> # Add a resource and wait for processing
        >>> result = client.add_resource(
        ...     "https://github.com/volcengine/OpenViking",
        ...     parent="viking://resources/",
        ...     wait=True
        ... )
        >>> 
        >>> # Search for context
        >>> results = client.find("what is openviking")
        >>> print(results)
        >>> 
        >>> client.close()

        Session-based conversation:
        
        >>> client = SyncOpenViking()
        >>> client.initialize()
        >>> 
        >>> # Create a session
        >>> session = client.session()
        >>> 
        >>> # Add conversation messages
        >>> client.add_message(session.session_id, "user", "I love Python!")
        >>> client.add_message(session.session_id, "assistant", "Python is great!")
        >>> 
        >>> # Commit session to extract memories
        >>> client.commit_session(session.session_id)
        >>> 
        >>> client.close()

    See Also:
        - AsyncOpenViking: Asynchronous version (recommended for asyncio)
        - SyncHTTPClient: HTTP client for remote server connection
        - Session: Session management class
    """

    def __init__(self, **kwargs):
        """
        Initialize SyncOpenViking client.

        Creates a synchronous OpenViking client that wraps AsyncOpenViking
        internally. All methods are blocking.

        Args:
            **kwargs: Arguments passed to AsyncOpenViking constructor:
                - path (str): Local storage path (optional)
                - Other configuration parameters

        Example:
            >>> # Use default configuration
            >>> client = SyncOpenViking()
            >>> 
            >>> # Specify custom storage path
            >>> client = SyncOpenViking(path="/data/openviking_workspace")

        Note:
            Call initialize() before using the client.
        """
        self._async_client = AsyncOpenViking(**kwargs)
        self._initialized = False

    def initialize(self) -> None:
        """
        Initialize OpenViking storage and indexes.

        This method must be called after creating the client and before using
        any other methods. It sets up the storage backend, creates necessary
        indexes, and starts background services.

        Example:
            >>> client = SyncOpenViking()
            >>> client.initialize()
            >>> # Now ready to use the client

        Note:
            This method is idempotent - calling it multiple times has no
            additional effect after the first call.
        """
        run_async(self._async_client.initialize())
        self._initialized = True

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> "Session":
        """
        Create a new session or load an existing one.

        Sessions are used to track conversations and interactions with the Agent.
        Each session has a unique ID and stores messages, usage records, and
        metadata.

        Args:
            session_id: Session ID to load. If None, creates a new session with
                an auto-generated UUID.
            must_exist: If True and session_id is provided, raises NotFoundError
                when the session does not exist. Default is False.

        Returns:
            Session: A Session object for tracking conversations.

        Example:
            >>> # Create a new session
            >>> session = client.session()
            >>> print(session.session_id)
            >>> 
            >>> # Load an existing session
            >>> session = client.session(session_id="existing-id", must_exist=True)

        See Also:
            - create_session(): Create a session and get metadata
            - add_message(): Add messages to a session
            - commit_session(): Extract memories from a session
        """
        return self._async_client.session(session_id, must_exist=must_exist)

    def session_exists(self, session_id: str) -> bool:
        """Check whether a session exists in storage."""
        return run_async(self._async_client.session_exists(session_id))

    def create_session(self) -> Dict[str, Any]:
        """Create a new session."""
        return run_async(self._async_client.create_session())

    def list_sessions(self) -> List[Any]:
        """List all sessions."""
        return run_async(self._async_client.list_sessions())

    def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        """Get session details."""
        return run_async(self._async_client.get_session(session_id, auto_create=auto_create))

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        run_async(self._async_client.delete_session(session_id))

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
    ) -> Dict[str, Any]:
        """
        Add a message to a session.

        Args:
            session_id: Session ID to add the message to.
            role: Message role, typically "user" or "assistant".
            content: Simple text content for the message.
            parts: Array of message parts for structured messages.
                If both content and parts are provided, parts takes precedence.

        Returns:
            Dict[str, Any]: Message metadata including message_id and timestamp.

        Example:
            >>> # Simple text message
            >>> result = client.add_message(
            ...     session_id="session-123",
            ...     role="user",
            ...     content="What is OpenViking?"
            ... )

        See Also:
            - commit_session(): Extract memories from session messages
        """
        return run_async(self._async_client.add_message(session_id, role, content, parts))

    def commit_session(
        self, session_id: str, telemetry: TelemetryRequest = False
    ) -> Dict[str, Any]:
        """
        Commit a session to archive messages and extract memories.

        Commits a session by archiving old messages and extracting long-term
        memories from the conversation. This enables the Agent to "learn"
        from interactions.

        Args:
            session_id: Session ID to commit.
            telemetry: Whether to attach operation telemetry data.
                Default is False.

        Returns:
            Dict[str, Any]: Commit result including memories_extracted count.

        Example:
            >>> # Add messages to session
            >>> client.add_message(session_id, "user", "I prefer Python")
            >>> client.add_message(session_id, "assistant", "Noted!")
            >>> 
            >>> # Commit to extract memories
            >>> result = client.commit_session(session_id)
            >>> print(f"Extracted {result['memories_extracted']['total']} memories")

        See Also:
            - add_message(): Add messages to a session before committing
        """
        return run_async(self._async_client.commit_session(session_id, telemetry=telemetry))

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Query background task status."""
        return run_async(self._async_client.get_task(task_id))

    def add_resource(
        self,
        path: str,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: float = None,
        build_index: bool = True,
        summarize: bool = False,
        telemetry: TelemetryRequest = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Add a resource (file, directory, or URL) to OpenViking.

        Adds a resource to the OpenViking context database. Resources are
        processed asynchronously to generate abstracts, overviews, and
        vector indexes for semantic search.

        Args:
            path: Local file path, directory path, or URL to add.
            to: Exact target URI for the resource (must not exist).
                Mutually exclusive with parent.
            parent: Parent URI under which to place the resource.
                Mutually exclusive with to.
            reason: Context or reason for adding this resource.
            instruction: Specific instructions for processing.
            wait: If True, wait for processing to complete. Default is False.
            timeout: Maximum time in seconds to wait if wait=True.
            build_index: Whether to build vector index. Default is True.
            summarize: Whether to generate summaries. Default is False.
            telemetry: Whether to attach telemetry data. Default is False.
            **kwargs: Additional options (ignore_dirs, include, exclude, etc.)

        Returns:
            Dict[str, Any]: Resource metadata including uri and task_id.

        Raises:
            ValueError: If both 'to' and 'parent' are specified.

        Example:
            >>> # Add a URL
            >>> result = client.add_resource(
            ...     "https://github.com/volcengine/OpenViking"
            ... )
            >>> 
            >>> # Add a local directory and wait
            >>> result = client.add_resource(
            ...     "/path/to/docs",
            ...     parent="viking://resources/",
            ...     wait=True
            ... )

        See Also:
            - find(): Search for context in resources
            - rm(): Remove a resource
        """
        if to and parent:
            raise ValueError("Cannot specify both 'to' and 'parent' at the same time.")
        return run_async(
            self._async_client.add_resource(
                path=path,
                to=to,
                parent=parent,
                reason=reason,
                instruction=instruction,
                wait=wait,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                telemetry=telemetry,
                **kwargs,
            )
        )

    def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: float = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        return run_async(
            self._async_client.add_skill(data, wait=wait, timeout=timeout, telemetry=telemetry)
        )

    def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional["Session"] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        telemetry: TelemetryRequest = False,
    ):
        """
        Execute a complex search with intent analysis and hierarchical retrieval.

        Performs a sophisticated search using directory recursive retrieval
        strategy for better context understanding.

        Args:
            query: Natural language search query.
            target_uri: Target directory URI to search within.
            session: Session object for context-aware search.
            session_id: Session ID string (alternative to session object).
            limit: Maximum number of results. Default is 10.
            score_threshold: Minimum similarity score threshold (0.0 to 1.0).
            filter: Metadata filters for narrowing results.
            telemetry: Whether to attach telemetry data. Default is False.

        Returns:
            FindResult: Search results with matching items and scores.

        Example:
            >>> results = client.search("how to use sessions?")
            >>> for result in results.results:
            ...     print(f"{result.uri}: {result.score}")

        Note:
            For simpler, faster searches, use find() instead.

        See Also:
            - find(): Quick semantic search
            - grep(): Content pattern matching
        """
        return run_async(
            self._async_client.search(
                query, target_uri, session, session_id, limit, score_threshold, filter, telemetry
            )
        )

    def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        telemetry: TelemetryRequest = False,
    ):
        """
        Execute a quick semantic search for relevant context.

        Performs a fast semantic search using vector similarity without
        intent analysis.

        Args:
            query: Natural language search query or keywords.
            target_uri: Target directory URI to search within.
            limit: Maximum number of results. Default is 10.
            score_threshold: Minimum similarity score threshold (0.0 to 1.0).
            filter: Metadata filters for narrowing results.
            telemetry: Whether to attach telemetry data. Default is False.

        Returns:
            FindResult: Search results with matching items.

        Example:
            >>> results = client.find("machine learning")
            >>> for result in results.results:
            ...     print(f"{result.uri}: {result.score:.3f}")

        Note:
            For sophisticated searches with intent analysis, use search().

        See Also:
            - search(): Complex search with intent analysis
            - grep(): Content pattern matching
        """
        return run_async(
            self._async_client.find(
                query,
                target_uri,
                limit,
                score_threshold,
                filter,
                telemetry,
            )
        )

    def abstract(self, uri: str) -> str:
        """
        Read the L0 abstract of a resource.

        Retrieves the one-sentence summary for quick identification.

        Args:
            uri: Viking URI of the resource or directory.

        Returns:
            str: The L0 abstract content as markdown text.

        Example:
            >>> abstract = client.abstract("viking://resources/my_project/")
            >>> print(abstract)  # One-sentence summary

        See Also:
            - overview(): Read the L1 overview for more detail
            - read(): Read the full L2 content
        """
        return run_async(self._async_client.abstract(uri))

    def overview(self, uri: str) -> str:
        """
        Read the L1 overview of a resource.

        Retrieves the detailed overview with core information and usage scenarios.

        Args:
            uri: Viking URI of the resource or directory.

        Returns:
            str: The L1 overview content as markdown text.

        Example:
            >>> overview = client.overview("viking://resources/my_project/")
            >>> print(overview)  # Detailed overview with key points

        See Also:
            - abstract(): Read the brief L0 abstract
            - read(): Read the full L2 content
        """
        return run_async(self._async_client.overview(uri))

    def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        """
        Read the full content of a file.

        Retrieves the L2 (details) layer - the complete original content.

        Args:
            uri: Viking URI of the file to read.
            offset: Line number to start reading from (0-indexed). Default is 0.
            limit: Maximum number of lines to read. -1 means read all lines.

        Returns:
            str: The full file content as text.

        Example:
            >>> # Read entire file
            >>> content = client.read("viking://resources/docs/api.md")
            >>> 
            >>> # Read first 100 lines
            >>> content = client.read("viking://resources/docs/api.md", limit=100)

        See Also:
            - abstract(): Read the L0 abstract
            - overview(): Read the L1 overview
        """
        return run_async(self._async_client.read(uri, offset=offset, limit=limit))

    def ls(self, uri: str, **kwargs) -> List[Any]:
        """
        List directory contents.

        Lists the contents of a directory in the OpenViking virtual filesystem.

        Args:
            uri: Viking URI of the directory to list.
            **kwargs: Additional options:
                - simple (bool): Return only relative path list. Default is False.
                - recursive (bool): List all subdirectories recursively. Default is False.
                - output (str): Output format. Default is "original".
                - abs_limit (int): Limit for abstract length. Default is 256.
                - show_all_hidden (bool): Show hidden files. Default is True.

        Returns:
            List[Any]: List of directory entries with name, uri, type, and abstract.

        Example:
            >>> # List resources
            >>> entries = client.ls("viking://resources/")
            >>> for entry in entries:
            ...     print(f"{entry['name']}: {entry['type']}")
            >>> 
            >>> # Recursive listing
            >>> entries = client.ls("viking://resources/", recursive=True)

        See Also:
            - tree(): Get directory tree structure
            - stat(): Get detailed resource status
        """
        return run_async(self._async_client.ls(uri, **kwargs))

    def link(self, from_uri: str, uris: Any, reason: str = "") -> None:
        """Create relation"""
        return run_async(self._async_client.link(from_uri, uris, reason))

    def unlink(self, from_uri: str, uri: str) -> None:
        """Delete relation"""
        return run_async(self._async_client.unlink(from_uri, uri))

    def export_ovpack(self, uri: str, to: str) -> str:
        """Export .ovpack file"""
        return run_async(self._async_client.export_ovpack(uri, to))

    def import_ovpack(
        self, file_path: str, target: str, force: bool = False, vectorize: bool = True
    ) -> str:
        """Import .ovpack file (triggers vectorization by default)"""
        return run_async(self._async_client.import_ovpack(file_path, target, force, vectorize))

    def close(self) -> None:
        """
        Close OpenViking and release all resources.

        This method should be called when you're done using the client to
        properly clean up resources, close database connections, and stop
        background services.

        Example:
            >>> client = SyncOpenViking()
            >>> client.initialize()
            >>> # ... use the client ...
            >>> client.close()  # Clean up when done
        """
        return run_async(self._async_client.close())

    def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations"""
        return run_async(self._async_client.relations(uri))

    def rm(self, uri: str, recursive: bool = False) -> None:
        """Delete resource"""
        return run_async(self._async_client.rm(uri, recursive))

    def wait_processed(self, timeout: float = None) -> Dict[str, Any]:
        """Wait for all async operations to complete"""
        return run_async(self._async_client.wait_processed(timeout))

    def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search"""
        return run_async(self._async_client.grep(uri, pattern, case_insensitive))

    def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching"""
        return run_async(self._async_client.glob(pattern, uri))

    def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource"""
        return run_async(self._async_client.mv(from_uri, to_uri))

    def tree(self, uri: str, **kwargs) -> Dict:
        """Get directory tree"""
        return run_async(self._async_client.tree(uri, **kwargs))

    def stat(self, uri: str) -> Dict:
        """Get resource status"""
        return run_async(self._async_client.stat(uri))

    def mkdir(self, uri: str) -> None:
        """Create directory"""
        return run_async(self._async_client.mkdir(uri))

    def get_status(self):
        """Get system status.

        Returns:
            SystemStatus containing health status of all components.
        """
        if not self._initialized:
            self.initialize()
        return self._async_client.get_status()

    def is_healthy(self) -> bool:
        """Quick health check.

        Returns:
            True if all components are healthy, False otherwise.
        """
        if not self._initialized:
            self.initialize()
        return self._async_client.is_healthy()

    @property
    def observer(self):
        """Get observer service for component status."""
        if not self._initialized:
            self.initialize()
        return self._async_client.observer

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        return run_async(AsyncOpenViking.reset())
