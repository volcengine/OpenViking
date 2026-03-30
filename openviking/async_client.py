# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async OpenViking client implementation (embedded mode only).

This module provides the asynchronous client for OpenViking in embedded mode,
which uses local storage and auto-starts services. For HTTP mode (connecting
to a remote OpenViking server), use AsyncHTTPClient or SyncHTTPClient from
openviking_cli.client instead.

The AsyncOpenViking class is implemented as a singleton, ensuring only one
instance exists per process. This is important for resource management and
preventing duplicate initialization.

Example:
    >>> import asyncio
    >>> from openviking import AsyncOpenViking
    >>>
    >>> async def main():
    ...     # Create client (singleton)
    ...     client = AsyncOpenViking(path="./my_workspace")
    ...     await client.initialize()
    ...
    ...     # Add a resource
    ...     result = await client.add_resource(
    ...         "https://github.com/example/repo",
    ...         wait=True,
    ...         reason="Project documentation"
    ...     )
    ...
    ...     # Search for context
    ...     results = await client.find("what is the main feature?")
    ...
    ...     # Create and manage a session
    ...     session = client.session()
    ...     await client.add_message(session.session_id, "user", "Hello!")
    ...     await client.commit_session(session.session_id)
    ...
    ...     # Clean up
    ...     await client.close()
    >>>
    >>> asyncio.run(main())
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Union

from openviking.client import LocalClient, Session
from openviking.service.debug_service import SystemStatus
from openviking.telemetry import TelemetryRequest
from openviking_cli.client.base import BaseClient
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class AsyncOpenViking:
    """
    OpenViking asynchronous client for embedded mode.

    This is the main client class for OpenViking in embedded mode, providing
    asynchronous access to all OpenViking features including resource management,
    semantic search, session handling, and memory extraction.

    The client is implemented as a singleton, ensuring only one instance exists
    per process. This is important for managing local storage, vector indexes,
    and background processing services efficiently.

    Key Features:
        - Resource Management: Add files, URLs, and repositories as resources
        - Semantic Search: Find relevant context using natural language queries
        - Session Management: Track conversations and extract memories
        - Filesystem Operations: Navigate and manage context like a filesystem

    Note:
        This client runs in embedded mode using local storage. For connecting
        to a remote OpenViking server, use AsyncHTTPClient or SyncHTTPClient
        from openviking_cli.client instead.

    Attributes:
        user (UserIdentifier): The current user identifier.
        observer: Observer service for component status monitoring.

    Examples:
        Basic usage with initialization:

        >>> from openviking import AsyncOpenViking
        >>> import asyncio
        >>>
        >>> async def main():
        ...     client = AsyncOpenViking(path="./my_workspace")
        ...     await client.initialize()
        ...
        ...     # Add a resource and wait for processing
        ...     result = await client.add_resource(
        ...         "https://github.com/volcengine/OpenViking",
        ...         parent="viking://resources/",
        ...         wait=True
        ...     )
        ...
        ...     # Search for context
        ...     results = await client.find("what is openviking")
        ...     print(results)
        ...
        ...     await client.close()
        >>>
        >>> asyncio.run(main())

        Session-based conversation with memory extraction:

        >>> async def chat_example():
        ...     client = AsyncOpenViking()
        ...     await client.initialize()
        ...
        ...     # Create a session
        ...     session = client.session()
        ...
        ...     # Add conversation messages
        ...     await client.add_message(session.session_id, "user", "I love Python!")
        ...     await client.add_message(session.session_id, "assistant", "Python is great!")
        ...
        ...     # Commit session to extract memories
        ...     await client.commit_session(session.session_id)
        ...
        ...     await client.close()

    See Also:
        - SyncOpenViking: Synchronous version of this client
        - AsyncHTTPClient: HTTP client for remote server connection
        - Session: Session management class
    """

    _instance: Optional["AsyncOpenViking"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = object.__new__(cls)
        return cls._instance

    def __init__(
        self,
        path: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize OpenViking client in embedded mode.

        Creates or retrieves the singleton instance of the OpenViking client.
        The client uses local storage for all data and automatically manages
        background services for semantic processing.

        Args:
            path: Local storage path for OpenViking data. If not provided,
                uses the path from the configuration file (ov.conf). The path
                will be created if it doesn't exist.
            **kwargs: Additional configuration parameters (currently unused,
                reserved for future extensions).

        Note:
            This is a singleton class. Calling the constructor multiple times
            will return the same instance. To reset the singleton (mainly for
            testing), use the reset() class method.

        Example:
            >>> # Use default configuration path
            >>> client = AsyncOpenViking()
            >>>
            >>> # Specify custom storage path
            >>> client = AsyncOpenViking(path="/data/openviking_workspace")
            >>>
            >>> # Initialize before use
            >>> await client.initialize()

        See Also:
            - initialize(): Must be called before using the client
            - close(): Clean up resources when done
            - reset(): Reset the singleton instance
        """
        # Singleton guard for repeated initialization
        if hasattr(self, "_singleton_initialized") and self._singleton_initialized:
            return

        self.user = UserIdentifier.the_default_user()
        self._initialized = False
        # Mark initialized only after LocalClient is successfully constructed.
        self._singleton_initialized = False

        self._client: BaseClient = LocalClient(
            path=path,
        )
        self._singleton_initialized = True

    # ============= Lifecycle methods =============

    async def initialize(self) -> None:
        """
        Initialize OpenViking storage and indexes.

        This method must be called after creating the client and before using
        any other methods. It sets up the storage backend, creates necessary
        indexes, and starts background services.

        The initialization process includes:
            - Creating or opening the local storage database
            - Setting up vector indexes for semantic search
            - Starting background processing queues
            - Initializing VLM and embedding model connections

        Raises:
            Exception: If initialization fails (e.g., invalid configuration,
                database errors, network issues for model access).

        Example:
            >>> client = AsyncOpenViking(path="./workspace")
            >>> await client.initialize()
            >>> # Now ready to use the client

        Note:
            This method is idempotent - calling it multiple times has no
            additional effect after the first call.
        """
        await self._client.initialize()
        self._initialized = True

    async def _ensure_initialized(self):
        """Ensure storage collections are initialized."""
        if not self._initialized:
            await self.initialize()

    async def close(self) -> None:
        """
        Close OpenViking and release all resources.

        This method should be called when you're done using the client to
        properly clean up resources, close database connections, and stop
        background services.

        After calling close(), the client instance should not be used anymore.
        To continue using OpenViking, create a new client instance.

        Example:
            >>> client = AsyncOpenViking()
            >>> await client.initialize()
            >>> # ... use the client ...
            >>> await client.close()  # Clean up when done

        Note:
            This method is automatically called when the singleton is reset
            via reset().
        """
        client = getattr(self, "_client", None)
        if client is not None:
            await client.close()
        self._initialized = False
        self._singleton_initialized = False

    @classmethod
    async def reset(cls) -> None:
        """
        Reset the singleton instance.

        This class method closes the current singleton instance and removes it,
        allowing a new instance to be created. This is mainly useful for testing
        scenarios where you need to reset the client state between tests.

        Warning:
            This method should not be used in production code. It's intended
            for testing purposes only.

        Example:
            >>> # In test code
            >>> await AsyncOpenViking.reset()
            >>> # Now a new instance can be created
            >>> client = AsyncOpenViking(path="./test_workspace")

        Note:
            This also resets the internal lock manager singleton.
        """
        with cls._lock:
            if cls._instance is not None:
                await cls._instance.close()
                cls._instance = None

        # Also reset lock manager singleton
        from openviking.storage.transaction import reset_lock_manager

        reset_lock_manager()

    # ============= Session methods =============

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> Session:
        """
        Create a new session or load an existing one.

        Sessions are used to track conversations and interactions with the Agent.
        Each session has a unique ID and stores messages, usage records, and
        metadata. Sessions can be committed to extract memories automatically.

        Args:
            session_id: Session ID to load. If None, creates a new session with
                an auto-generated UUID. If provided and the session exists,
                loads that session; otherwise creates a new session with that ID.
            must_exist: If True and session_id is provided, raises NotFoundError
                when the session does not exist. If session_id is None, this
                parameter is ignored. Default is False.

        Returns:
            Session: A Session object that can be used to track conversations
                and extract memories.

        Raises:
            NotFoundError: If must_exist=True and the session does not exist.

        Example:
            >>> # Create a new session with auto-generated ID
            >>> session = client.session()
            >>> print(session.session_id)  # e.g., "abc123..."
            >>>
            >>> # Create a session with a specific ID
            >>> session = client.session(session_id="my-session-001")
            >>>
            >>> # Load an existing session (raises error if not found)
            >>> session = client.session(session_id="existing-id", must_exist=True)

        See Also:
            - create_session(): Create a session and get its metadata
            - get_session(): Get session details without loading
            - session_exists(): Check if a session exists
        """
        return self._client.session(session_id, must_exist=must_exist)

    async def session_exists(self, session_id: str) -> bool:
        """
        Check whether a session exists in storage.

        Args:
            session_id: Session ID to check.

        Returns:
            bool: True if the session exists, False otherwise.

        Example:
            >>> exists = await client.session_exists("session-123")
            >>> if exists:
            ...     session = client.session(session_id="session-123", must_exist=True)
        """
        await self._ensure_initialized()
        return await self._client.session_exists(session_id)

    async def create_session(self) -> Dict[str, Any]:
        """
        Create a new session and return its metadata.

        Creates a new session with an auto-generated UUID and returns the
        session metadata including the session ID.

        Returns:
            Dict[str, Any]: Session metadata including:
                - session_id (str): The unique session identifier
                - created_at (str): ISO timestamp of creation
                - message_count (int): Initial message count (0)
                - commit_count (int): Initial commit count (0)

        Example:
            >>> metadata = await client.create_session()
            >>> session_id = metadata["session_id"]
            >>> session = client.session(session_id)

        See Also:
            - session(): Load or create a session with Session object
            - get_session(): Get metadata for an existing session
        """
        await self._ensure_initialized()
        return await self._client.create_session()

    async def list_sessions(self) -> List[Any]:
        """
        List all sessions in the storage.

        Returns:
            List[Any]: A list of session metadata dictionaries, each containing:
                - session_id (str): Unique session identifier
                - created_at (str): ISO timestamp of creation
                - updated_at (str): ISO timestamp of last update
                - message_count (int): Number of messages in the session
                - commit_count (int): Number of times the session was committed

        Example:
            >>> sessions = await client.list_sessions()
            >>> for session in sessions:
            ...     print(f"Session {session['session_id']}: {session['message_count']} messages")
        """
        await self._ensure_initialized()
        return await self._client.list_sessions()

    async def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        """
        Get session details by session ID.

        Retrieves the metadata for an existing session, or optionally creates
        a new session if it doesn't exist.

        Args:
            session_id: Session ID to retrieve.
            auto_create: If True and the session doesn't exist, create it.
                Default is False.

        Returns:
            Dict[str, Any]: Session metadata including:
                - session_id (str): Unique session identifier
                - created_at (str): ISO timestamp of creation
                - updated_at (str): ISO timestamp of last update
                - message_count (int): Number of messages in the session
                - commit_count (int): Number of times the session was committed
                - memories_extracted (dict): Count of extracted memories by category
                - llm_token_usage (dict): Token usage statistics

        Raises:
            NotFoundError: If the session doesn't exist and auto_create is False.

        Example:
            >>> # Get existing session (raises error if not found)
            >>> metadata = await client.get_session("session-123")
            >>>
            >>> # Get or create session
            >>> metadata = await client.get_session("session-123", auto_create=True)
        """
        await self._ensure_initialized()
        return await self._client.get_session(session_id, auto_create=auto_create)

    async def get_session_context(
        self, session_id: str, token_budget: int = 128_000
    ) -> Dict[str, Any]:
        """Get assembled session context."""
        await self._ensure_initialized()
        return await self._client.get_session_context(session_id, token_budget=token_budget)

    async def get_session_archive(self, session_id: str, archive_id: str) -> Dict[str, Any]:
        """Get one completed archive for a session."""
        await self._ensure_initialized()
        return await self._client.get_session_archive(session_id, archive_id)

    async def delete_session(self, session_id: str) -> None:
        """
        Delete a session and all its data.

        Permanently removes a session including all its messages, usage records,
        and metadata. This operation cannot be undone.

        Args:
            session_id: Session ID to delete.

        Warning:
            This operation is irreversible. All session data will be permanently
            deleted.

        Example:
            >>> await client.delete_session("session-123")

        See Also:
            - session_exists(): Check if a session exists before deletion
        """
        await self._ensure_initialized()
        await self._client.delete_session(session_id)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
    ) -> Dict[str, Any]:
        """
        Add a message to a session.

        Adds a new message to the specified session. Messages can be simple text
        or structured with multiple parts (text, context, tool calls, etc.).

        Args:
            session_id: Session ID to add the message to.
            role: Message role, typically "user" or "assistant".
            content: Simple text content. Use this for basic text messages.
                Mutually exclusive with parts; if both are provided, parts
                takes precedence.
            parts: Array of message parts for structured messages. Each part
                can be a TextPart, ContextPart, or ToolPart. Use this for
                complex messages with multiple components.

        Returns:
            Dict[str, Any]: Message metadata including:
                - message_id (str): Unique message identifier
                - timestamp (str): ISO timestamp of message creation

        Example:
            >>> # Simple text message
            >>> result = await client.add_message(
            ...     session_id="session-123",
            ...     role="user",
            ...     content="What is OpenViking?"
            ... )
            >>>
            >>> # Structured message with parts
            >>> result = await client.add_message(
            ...     session_id="session-123",
            ...     role="assistant",
            ...     parts=[
            ...         {"type": "text", "content": "OpenViking is..."},
            ...         {"type": "context", "uri": "viking://resources/docs"}
            ...     ]
            ... )

        Note:
            If both content and parts are provided, parts takes precedence.

        See Also:
            - commit_session(): Extract memories from session messages
            - Session: Session object with message handling
        """
        await self._ensure_initialized()
        return await self._client.add_message(
            session_id=session_id, role=role, content=content, parts=parts
        )

    async def commit_session(
        self, session_id: str, telemetry: TelemetryRequest = False
    ) -> Dict[str, Any]:
        """
        Commit a session to archive messages and extract memories.

        Commits a session by archiving old messages and extracting long-term
        memories from the conversation. This process enables the Agent to
        "learn" from interactions and improve over time.

        The commit process includes:
            - Compressing conversation history when it exceeds thresholds
            - Extracting user memories (preferences, entities, events, etc.)
            - Extracting agent memories (patterns, skills, tool usage)
            - Updating session metadata with extraction statistics

        Args:
            session_id: Session ID to commit.
            telemetry: Whether to attach operation telemetry data to the result.
                Default is False.

        Returns:
            Dict[str, Any]: Commit result including:
                - session_id (str): The committed session ID
                - compressed (bool): Whether compression occurred
                - memories_extracted (dict): Count of memories by category:
                    - profile: User profile information
                    - preferences: User preferences
                    - entities: Important entities mentioned
                    - events: Events and appointments
                    - cases: Problem-solution patterns
                    - patterns: Behavioral patterns
                    - tools: Tool usage experiences
                    - skills: Skill usage experiences
                    - total: Total memories extracted

        Example:
            >>> # Add messages to session
            >>> await client.add_message(session_id, "user", "I prefer Python")
            >>> await client.add_message(session_id, "assistant", "Noted!")
            >>>
            >>> # Commit to extract memories
            >>> result = await client.commit_session(session_id)
            >>> print(f"Extracted {result['memories_extracted']['total']} memories")

        Note:
            After commit, older messages may be compressed to save context space,
            but the extracted memories remain accessible for future sessions.

        See Also:
            - add_message(): Add messages to a session before committing
            - Session: Session object with message handling
        """
        await self._ensure_initialized()
        return await self._client.commit_session(session_id, telemetry=telemetry)

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Query background task status."""
        await self._ensure_initialized()
        return await self._client.get_task(task_id)

    # ============= Resource methods =============

    async def add_resource(
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
        watch_interval: float = 0,
        telemetry: TelemetryRequest = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Add a resource (file, directory, or URL) to OpenViking.

        Adds a resource to the OpenViking context database. Resources are
        processed asynchronously to generate L0 abstracts, L1 overviews, and
        vector indexes for semantic search.

        Resources are organized in a virtual filesystem under viking:// URIs:
            - viking://resources/ - General resources (docs, repos, web pages)
            - viking://user/{user_id}/ - User-specific resources
            - viking://agent/{agent_id}/ - Agent-specific resources

        Args:
            path: Local file path, directory path, or URL to add. Supports:
                - Local files: "/path/to/file.md"
                - Local directories: "/path/to/project/"
                - URLs: "https://example.com/docs"
                - GitHub repos: "https://github.com/user/repo"
            to: Exact target URI for the resource. The URI must not already
                exist. Mutually exclusive with parent.
            parent: Parent URI under which to place the resource. The parent
                must already exist. Mutually exclusive with to.
            reason: Context or reason for adding this resource. This helps
                the system understand the resource's purpose.
            instruction: Specific instructions for how to process the resource.
                Can include parsing preferences or processing hints.
            wait: If True, wait for processing to complete before returning.
                Default is False (async processing).
            timeout: Maximum time in seconds to wait if wait=True. None means
                no timeout.
            build_index: Whether to build vector index for semantic search.
                Default is True. Set to False for resources that don't need
                search capability.
            summarize: Whether to generate summaries. Default is False.
            watch_interval: For watching file changes (future feature).
                Default is 0 (disabled).
            telemetry: Whether to attach operation telemetry data.
                Default is False.
            **kwargs: Additional options passed to the parser chain:
                - strict: Enable strict parsing mode
                - ignore_dirs: List of directory names to ignore
                - include: List of file patterns to include
                - exclude: List of file patterns to exclude

        Returns:
            Dict[str, Any]: Resource metadata including:
                - uri (str): The Viking URI of the added resource
                - task_id (str): Background task ID for tracking processing
                - status (str): Initial processing status

        Raises:
            ValueError: If both 'to' and 'parent' are specified.
            Exception: If the path doesn't exist or processing fails.

        Example:
            >>> # Add a URL with default settings
            >>> result = await client.add_resource(
            ...     "https://github.com/volcengine/OpenViking"
            ... )
            >>> print(f"Resource added at: {result['uri']}")
            >>>
            >>> # Add a local directory and wait for processing
            >>> result = await client.add_resource(
            ...     "/path/to/my/docs",
            ...     parent="viking://resources/projects/",
            ...     wait=True,
            ...     timeout=60
            ... )
            >>>
            >>> # Add with custom parsing options
            >>> result = await client.add_resource(
            ...     "/path/to/repo",
            ...     ignore_dirs=["node_modules", ".git"],
            ...     exclude=["*.log", "*.tmp"]
            ... )

        Note:
            Resources are processed asynchronously by default. Use wait=True
            or check task status with get_task() to ensure processing completes.

        See Also:
            - get_task(): Check background task status
            - wait_processed(): Wait for all queued processing
            - rm(): Remove a resource
            - find(): Search for context in resources
        """
        await self._ensure_initialized()

        if to and parent:
            raise ValueError("Cannot specify both 'to' and 'parent' at the same time.")

        return await self._client.add_resource(
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
            watch_interval=watch_interval,
            **kwargs,
        )

    @property
    def _service(self):
        return self._client.service

    async def wait_processed(self, timeout: float = None) -> Dict[str, Any]:
        """Wait for all queued processing to complete."""
        await self._ensure_initialized()
        return await self._client.wait_processed(timeout=timeout)

    async def build_index(self, resource_uris: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """
        Manually trigger index building for resources.

        Args:
            resource_uris: Single URI or list of URIs to index.
        """
        await self._ensure_initialized()
        return await self._client.build_index(resource_uris, **kwargs)

    async def summarize(self, resource_uris: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """
        Manually trigger summarization for resources.

        Args:
            resource_uris: Single URI or list of URIs to summarize.
        """
        await self._ensure_initialized()
        return await self._client.summarize(resource_uris, **kwargs)

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: float = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking.

        Args:
            wait: Whether to wait for vectorization to complete
            timeout: Wait timeout in seconds
        """
        await self._ensure_initialized()
        return await self._client.add_skill(
            data=data,
            wait=wait,
            timeout=timeout,
            telemetry=telemetry,
        )

    # ============= Search methods =============

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional[Union["Session", Any]] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        telemetry: TelemetryRequest = False,
    ):
        """
        Execute a complex search with intent analysis and hierarchical retrieval.

        Performs a sophisticated search using the directory recursive retrieval
        strategy. This method:
            1. Analyzes the query to generate multiple search intents
            2. Uses vector search to locate high-score directories
            3. Performs secondary retrieval within those directories
            4. Recursively drills down into subdirectories
            5. Aggregates and returns the most relevant results

        This approach provides better context understanding than simple semantic
        search by considering the hierarchical structure of resources.

        Args:
            query: Natural language search query. Can be a question or keywords.
            target_uri: Target directory URI to search within. If empty, searches
                all resources. Examples: "viking://resources/my_project/",
                "viking://user/alice/memories/".
            session: Session object for context-aware search. The session's
                conversation history helps improve search relevance.
                Mutually exclusive with session_id.
            session_id: Session ID string (alternative to session object).
                Mutually exclusive with session.
            limit: Maximum number of results to return. Default is 10.
            score_threshold: Minimum similarity score threshold (0.0 to 1.0).
                Results below this threshold are filtered out. None means no
                threshold (default).
            filter: Metadata filters for narrowing results. Supports:
                - {"type": "file"} - Only files
                - {"type": "directory"} - Only directories
                - {"category": "docs"} - Custom metadata filters
            telemetry: Whether to attach operation telemetry data.
                Default is False.

        Returns:
            FindResult: Search results including:
                - results (List[SearchResult]): List of matching items, each with:
                    - uri (str): Viking URI of the matching resource
                    - score (float): Similarity score
                    - content (str): Matching content snippet
                    - metadata (dict): Resource metadata
                - query (str): Original query
                - trajectory (List[str]): Retrieval trajectory for debugging

        Example:
            >>> # Simple search
            >>> results = await client.search("how to use sessions?")
            >>> for result in results.results:
            ...     print(f"{result.uri}: {result.score}")
            >>>
            >>> # Search within a specific directory
            >>> results = await client.search(
            ...     query="API authentication",
            ...     target_uri="viking://resources/docs/api/"
            ... )
            >>>
            >>> # Context-aware search with session
            >>> session = client.session()
            >>> await client.add_message(session.session_id, "user", "I'm working on auth")
            >>> results = await client.search(
            ...     query="implementation details",
            ...     session=session
            ... )
            >>>
            >>> # Search with filters
            >>> results = await client.search(
            ...     query="error handling",
            ...     filter={"type": "file", "language": "python"}
            ... )

        Note:
            For simpler, faster searches without intent analysis, use find().

        See Also:
            - find(): Quick semantic search without intent analysis
            - grep(): Content search with pattern matching
            - glob(): File pattern matching
        """
        await self._ensure_initialized()
        sid = session_id or (session.session_id if session else None)
        return await self._client.search(
            query=query,
            target_uri=target_uri,
            session_id=sid,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
            telemetry=telemetry,
        )

    async def find(
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

        Performs a fast semantic search using vector similarity without the
        intent analysis and hierarchical retrieval of search(). This is suitable
        for simple queries where you want quick results.

        Args:
            query: Natural language search query or keywords.
            target_uri: Target directory URI to search within. If empty,
                searches all resources.
            limit: Maximum number of results to return. Default is 10.
            score_threshold: Minimum similarity score threshold (0.0 to 1.0).
                None means no threshold.
            filter: Metadata filters for narrowing results.
            telemetry: Whether to attach operation telemetry data.
                Default is False.

        Returns:
            FindResult: Search results including:
                - results (List[SearchResult]): List of matching items
                - query (str): Original query

        Example:
            >>> # Quick search across all resources
            >>> results = await client.find("machine learning")
            >>> for result in results.results:
            ...     print(f"{result.uri}: {result.score:.3f}")
            >>>
            >>> # Search in a specific directory
            >>> results = await client.find(
            ...     query="authentication",
            ...     target_uri="viking://resources/docs/"
            ... )
            >>>
            >>> # Search with score threshold
            >>> results = await client.find(
            ...     query="API reference",
            ...     score_threshold=0.7
            ... )

        Note:
            For more sophisticated searches with intent analysis and better
            context understanding, use search() instead.

        See Also:
            - search(): Complex search with intent analysis
            - grep(): Content search with pattern matching
            - glob(): File pattern matching
        """
        await self._ensure_initialized()
        return await self._client.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
            telemetry=telemetry,
        )

    # ============= FS methods =============

    async def abstract(self, uri: str) -> str:
        """
        Read the L0 abstract of a resource.

        Retrieves the L0 (abstract) layer of a resource, which is a one-sentence
        summary for quick identification and relevance checking. This is the
        highest-level summary in OpenViking's tiered context system.

        The abstract layer is designed to be very concise (~100 tokens) for
        quick scanning when searching or browsing resources.

        Args:
            uri: Viking URI of the resource or directory.

        Returns:
            str: The L0 abstract content as markdown text.

        Example:
            >>> abstract = await client.abstract("viking://resources/my_project/")
            >>> print(abstract)  # One-sentence summary

        Note:
            Every resource and directory in OpenViking has an L0 abstract
            generated automatically during processing.

        See Also:
            - overview(): Read the L1 overview for more detail
            - read(): Read the full L2 content
        """
        await self._ensure_initialized()
        return await self._client.abstract(uri)

    async def overview(self, uri: str) -> str:
        """
        Read the L1 overview of a resource.

        Retrieves the L1 (overview) layer of a resource, which contains the
        core information and usage scenarios. This layer is designed for
        Agent decision-making during the planning phase.

        The overview layer is more detailed than abstract (~2k tokens) and
        includes key points, structure, and important context without the
        full detail.

        Args:
            uri: Viking URI of the resource or directory.

        Returns:
            str: The L1 overview content as markdown text.

        Example:
            >>> overview = await client.overview("viking://resources/my_project/")
            >>> print(overview)  # Detailed overview with key points

        Note:
            The overview is generated automatically during resource processing
            and is designed to provide enough context for decision-making.

        See Also:
            - abstract(): Read the brief L0 abstract
            - read(): Read the full L2 content
        """
        await self._ensure_initialized()
        return await self._client.overview(uri)

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        """
        Read the full content of a file.

        Retrieves the L2 (details) layer of a resource, which is the complete
        original content. This is the most detailed layer and should be used
        when deep reading is necessary.

        Args:
            uri: Viking URI of the file to read.
            offset: Line number to start reading from (0-indexed). Default is 0.
            limit: Maximum number of lines to read. -1 means read all lines.
                Default is -1.

        Returns:
            str: The full file content as text.

        Example:
            >>> # Read entire file
            >>> content = await client.read("viking://resources/docs/api.md")
            >>> print(content)
            >>>
            >>> # Read first 100 lines
            >>> content = await client.read("viking://resources/docs/api.md", limit=100)
            >>>
            >>> # Read from line 50 onwards
            >>> content = await client.read("viking://resources/docs/api.md", offset=50)

        Note:
            For directories, use abstract() or overview() to get summaries.
            The read() method is primarily for files.

        See Also:
            - abstract(): Read the L0 abstract
            - overview(): Read the L1 overview
            - ls(): List directory contents
        """
        await self._ensure_initialized()
        return await self._client.read(uri, offset=offset, limit=limit)

    async def ls(self, uri: str, **kwargs) -> List[Any]:
        """
        List directory contents.

        Lists the contents of a directory in the OpenViking virtual filesystem.
        Similar to the Unix 'ls' command, this method shows files and
        subdirectories within a given URI.

        Args:
            uri: Viking URI of the directory to list. Examples:
                - "viking://resources/" - List all resources
                - "viking://user/alice/memories/" - List user memories
                - "viking://agent/my-agent/skills/" - List agent skills
            **kwargs: Additional options:
                - simple (bool): Return only relative path list. Default is False.
                - recursive (bool): List all subdirectories recursively. Default is False.
                - output (str): Output format ("original" or "json"). Default is "original".
                - abs_limit (int): Limit for abstract length. Default is 256.
                - show_all_hidden (bool): Show hidden files. Default is True.

        Returns:
            List[Any]: List of directory entries. Each entry contains:
                - name (str): File or directory name
                - uri (str): Full Viking URI
                - type (str): "file" or "directory"
                - abstract (str): L0 abstract (if available)
                - metadata (dict): Additional metadata

        Example:
            >>> # List resources
            >>> entries = await client.ls("viking://resources/")
            >>> for entry in entries:
            ...     print(f"{entry['name']}: {entry['type']}")
            >>>
            >>> # Recursive listing
            >>> entries = await client.ls("viking://resources/", recursive=True)
            >>>
            >>> # Simple path list
            >>> paths = await client.ls("viking://resources/", simple=True)

        See Also:
            - tree(): Get directory tree structure
            - stat(): Get detailed resource status
            - abstract(): Get abstract of specific resource
        """
        await self._ensure_initialized()
        recursive = kwargs.get("recursive", False)
        simple = kwargs.get("simple", False)
        output = kwargs.get("output", "original")
        abs_limit = kwargs.get("abs_limit", 256)
        show_all_hidden = kwargs.get("show_all_hidden", True)
        return await self._client.ls(
            uri,
            recursive=recursive,
            simple=simple,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
        )

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """
        Remove a resource or directory.

        Deletes a resource or directory from OpenViking. This removes the
        resource from the virtual filesystem and deletes associated indexes
        and metadata.

        Args:
            uri: Viking URI of the resource or directory to remove.
            recursive: If True, remove directory and all its contents recursively.
                Required for non-empty directories. Default is False.

        Warning:
            This operation is irreversible. All data, indexes, and metadata
            associated with the resource will be permanently deleted.

        Example:
            >>> # Remove a file
            >>> await client.rm("viking://resources/old_doc.md")
            >>>
            >>> # Remove a directory and all its contents
            >>> await client.rm("viking://resources/old_project/", recursive=True)

        Note:
            For non-empty directories, you must set recursive=True or the
            operation will fail.

        See Also:
            - add_resource(): Add resources to OpenViking
            - mv(): Move resources to a new location
        """
        await self._ensure_initialized()
        await self._client.rm(uri, recursive=recursive)

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """
        Search for a pattern within resource contents.

        Performs a content search (similar to Unix grep) for a text pattern
        within the specified resource or directory.

        Args:
            uri: Viking URI of the resource or directory to search.
            pattern: Text pattern to search for. Supports regular expressions.
            case_insensitive: If True, perform case-insensitive search.
                Default is False.

        Returns:
            Dict: Search results including:
                - matches (List[Dict]): List of matches, each containing:
                    - uri (str): URI of the file with the match
                    - line_number (int): Line number of the match
                    - line (str): The matching line content
                    - context (str): Surrounding context

        Example:
            >>> # Search for "error" in a directory
            >>> results = await client.grep("viking://resources/docs/", "error")
            >>> for match in results["matches"]:
            ...     print(f"{match['uri']}:{match['line_number']}: {match['line']}")
            >>>
            >>> # Case-insensitive search
            >>> results = await client.grep(
            ...     "viking://resources/",
            ...     pattern="TODO",
            ...     case_insensitive=True
            ... )

        Note:
            This performs text-based pattern matching, not semantic search.
            For semantic search, use find() or search().

        See Also:
            - find(): Semantic search
            - search(): Complex search with intent analysis
            - glob(): File pattern matching
        """
        await self._ensure_initialized()
        return await self._client.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """
        Match files using glob patterns.

        Performs file pattern matching (similar to Unix glob) to find files
        matching a specific pattern.

        Args:
            pattern: Glob pattern to match. Supports:
                - *: Match any sequence of characters
                - **: Match any sequence of characters including directory separators
                - ?: Match any single character
                - [seq]: Match any character in sequence
                - [!seq]: Match any character not in sequence
            uri: Base URI to search from. Default is "viking://" (search all).

        Returns:
            Dict: Matching files including:
                - matches (List[str]): List of matching URIs

        Example:
            >>> # Find all Python files
            >>> results = await client.glob("**/*.py")
            >>> for uri in results["matches"]:
            ...     print(uri)
            >>>
            >>> # Find all markdown files in a specific directory
            >>> results = await client.glob(
            ...     "**/*.md",
            ...     uri="viking://resources/docs/"
            ... )
            >>>
            >>> # Find files matching a pattern
            >>> results = await client.glob("**/test_*.py")

        Note:
            This performs path-based pattern matching, not semantic search.

        See Also:
            - find(): Semantic search
            - grep(): Content pattern matching
            - ls(): List directory contents
        """
        await self._ensure_initialized()
        return await self._client.glob(pattern, uri=uri)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """
        Move a resource to a new location.

        Moves a resource from one URI to another within the OpenViking
        virtual filesystem. This operation preserves all metadata and indexes.

        Args:
            from_uri: Source Viking URI of the resource to move.
            to_uri: Destination Viking URI for the resource.

        Example:
            >>> # Move a file to a new location
            >>> await client.mv(
            ...     "viking://resources/docs/old_name.md",
            ...     "viking://resources/docs/new_name.md"
            ... )
            >>>
            >>> # Move to a different directory
            >>> await client.mv(
            ...     "viking://resources/temp/file.md",
            ...     "viking://resources/docs/file.md"
            ... )

        Note:
            The destination must not already exist. Use rm() first if you
            want to overwrite.

        See Also:
            - rm(): Remove a resource
            - ls(): List directory contents
        """
        await self._ensure_initialized()
        await self._client.mv(from_uri, to_uri)

    async def tree(self, uri: str, **kwargs) -> Dict:
        """
        Get the directory tree structure.

        Returns a tree representation of a directory and its subdirectories,
        similar to the Unix 'tree' command.

        Args:
            uri: Viking URI of the root directory.
            **kwargs: Additional options:
                - output (str): Output format ("original" or "json"). Default is "original".
                - abs_limit (int): Limit for abstract length. Default is 128.
                - show_all_hidden (bool): Show hidden files. Default is True.
                - node_limit (int): Maximum number of nodes to include. Default is 1000.

        Returns:
            Dict: Tree structure including:
                - name (str): Root directory name
                - uri (str): Root directory URI
                - children (List[Dict]): Child nodes (files and directories)
                - abstract (str): L0 abstract (if available)

        Example:
            >>> # Get tree of a resource directory
            >>> tree = await client.tree("viking://resources/my_project/")
            >>> print(tree)
            >>>
            >>> # Limit the tree depth
            >>> tree = await client.tree(
            ...     "viking://resources/",
            ...     node_limit=500
            ... )

        See Also:
            - ls(): List directory contents
            - stat(): Get resource status
        """
        await self._ensure_initialized()
        output = kwargs.get("output", "original")
        abs_limit = kwargs.get("abs_limit", 128)
        show_all_hidden = kwargs.get("show_all_hidden", True)
        node_limit = kwargs.get("node_limit", 1000)
        return await self._client.tree(
            uri,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
        )

    async def mkdir(self, uri: str) -> None:
        """
        Create a directory.

        Creates a new directory in the OpenViking virtual filesystem.

        Args:
            uri: Viking URI for the new directory. Parent directories must
                exist unless using recursive creation (not yet implemented).

        Example:
            >>> # Create a new directory
            >>> await client.mkdir("viking://resources/new_project/")
            >>>
            >>> # Create nested directories (if supported)
            >>> await client.mkdir("viking://resources/project/subdir/")

        Note:
            Parent directories must already exist. For nested directory creation,
            create parent directories first.

        See Also:
            - ls(): List directory contents
            - rm(): Remove a directory
        """
        await self._ensure_initialized()
        await self._client.mkdir(uri)

    async def stat(self, uri: str) -> Dict:
        """
        Get detailed status and metadata of a resource.

        Returns detailed information about a resource including its type,
        size, creation time, and other metadata.

        Args:
            uri: Viking URI of the resource.

        Returns:
            Dict: Resource status including:
                - uri (str): Resource URI
                - type (str): "file" or "directory"
                - name (str): Resource name
                - size (int): Size in bytes (for files)
                - created_at (str): ISO timestamp of creation
                - updated_at (str): ISO timestamp of last update
                - metadata (dict): Additional metadata

        Example:
            >>> status = await client.stat("viking://resources/my_project/")
            >>> print(f"Type: {status['type']}")
            >>> print(f"Created: {status['created_at']}")

        See Also:
            - ls(): List directory contents
            - tree(): Get directory tree
        """
        await self._ensure_initialized()
        return await self._client.stat(uri)

    # ============= Relation methods =============

    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """
        Get relations for a resource.

        Retrieves all outgoing relations from a resource. Relations represent
        connections between resources, such as dependencies, references, or
        custom relationships.

        Args:
            uri: Viking URI of the resource.

        Returns:
            List[Dict[str, Any]]: List of relations, each containing:
                - uri (str): Target resource URI
                - reason (str): Reason or type of the relation

        Example:
            >>> relations = await client.relations("viking://resources/doc.md")
            >>> for rel in relations:
            ...     print(f"Related to: {rel['uri']} (reason: {rel['reason']})")

        See Also:
            - link(): Create a relation
            - unlink(): Remove a relation
        """
        await self._ensure_initialized()
        return await self._client.relations(uri)

    async def link(self, from_uri: str, uris: Any, reason: str = "") -> None:
        """
        Create a relation between resources.

        Creates one or more relations from a source resource to target resources.
        Relations can represent dependencies, references, or custom relationships.

        Args:
            from_uri: Source Viking URI.
            uris: Target URI or list of target URIs. Can be a single URI string
                or a list of URI strings.
            reason: Reason or type of the relation. This helps describe the
                relationship between resources. Default is empty string.

        Example:
            >>> # Create a single relation
            >>> await client.link(
            ...     "viking://resources/docs/guide.md",
            ...     "viking://resources/docs/api.md",
            ...     reason="references"
            ... )
            >>>
            >>> # Create multiple relations
            >>> await client.link(
            ...     "viking://resources/project/",
            ...     [
            ...         "viking://resources/docs/api.md",
            ...         "viking://resources/docs/tutorial.md"
            ...     ],
            ...     reason="depends on"
            ... )

        See Also:
            - relations(): Get all relations for a resource
            - unlink(): Remove a relation
        """
        await self._ensure_initialized()
        await self._client.link(from_uri, uris, reason)

    async def unlink(self, from_uri: str, uri: str) -> None:
        """
        Remove a relation between resources.

        Removes a specific relation from a source resource to a target resource.

        Args:
            from_uri: Source Viking URI.
            uri: Target URI to remove from the relations.

        Example:
            >>> # Remove a relation
            >>> await client.unlink(
            ...     "viking://resources/docs/guide.md",
            ...     "viking://resources/docs/old_api.md"
            ... )

        Note:
            This only removes the relation, not the resources themselves.

        See Also:
            - link(): Create a relation
            - relations(): Get all relations for a resource
        """
        await self._ensure_initialized()
        await self._client.unlink(from_uri, uri)

    # ============= Pack methods =============

    async def export_ovpack(self, uri: str, to: str) -> str:
        """
        Export resources as an .ovpack file.

        Exports a resource or directory and all its contents to a portable
        .ovpack file format. This is useful for backing up resources or
        sharing them with others.

        Args:
            uri: Viking URI of the resource or directory to export.
            to: Local file path for the output .ovpack file. The file will
                be created if it doesn't exist.

        Returns:
            str: Path to the exported .ovpack file.

        Example:
            >>> # Export a project
            >>> path = await client.export_ovpack(
            ...     "viking://resources/my_project/",
            ...     "/backup/my_project.ovpack"
            ... )
            >>> print(f"Exported to: {path}")

        Note:
            The .ovpack format includes all content, metadata, and indexes
            for the exported resources.

        See Also:
            - import_ovpack(): Import an .ovpack file
        """
        await self._ensure_initialized()
        return await self._client.export_ovpack(uri, to)

    async def import_ovpack(
        self, file_path: str, parent: str, force: bool = False, vectorize: bool = True
    ) -> str:
        """
        Import an .ovpack file into OpenViking.

        Imports resources from a .ovpack file into the OpenViking virtual
        filesystem. This is useful for restoring backups or importing shared
        resources.

        Args:
            file_path: Local path to the .ovpack file to import.
            parent: Target parent URI where the resources will be imported.
                Example: "viking://resources/imported/"
            force: If True, overwrite existing resources with the same name.
                Default is False (raises error if resource exists).
            vectorize: If True, trigger vectorization for imported resources.
                Default is True. Set to False for faster import without
                immediate search capability.

        Returns:
            str: Viking URI of the imported root resource.

        Example:
            >>> # Import a backup
            >>> uri = await client.import_ovpack(
            ...     "/backup/my_project.ovpack",
            ...     "viking://resources/restored/"
            ... )
            >>> print(f"Imported to: {uri}")
            >>>
            >>> # Force import (overwrite existing)
            >>> uri = await client.import_ovpack(
            ...     "/backup/project.ovpack",
            ...     "viking://resources/project/",
            ...     force=True
            ... )

        Note:
            The .ovpack format preserves all content, metadata, and indexes
            from the original export.

        See Also:
            - export_ovpack(): Export resources as .ovpack file
        """
        await self._ensure_initialized()
        return await self._client.import_ovpack(file_path, parent, force=force, vectorize=vectorize)

    # ============= Debug methods =============

    def get_status(self) -> Union[SystemStatus, Dict[str, Any]]:
        """
        Get system status and health information.

        Returns the current status of all OpenViking components including
        storage, indexes, and background services.

        Returns:
            Union[SystemStatus, Dict[str, Any]]: System status including:
                - storage (dict): Storage backend status
                - indexes (dict): Vector index status
                - services (dict): Background service status
                - healthy (bool): Overall health status

        Example:
            >>> status = client.get_status()
            >>> print(f"System healthy: {status.get('healthy', False)}")

        Note:
            This method will auto-initialize the client if not already initialized.

        See Also:
            - is_healthy(): Quick health check
        """
        return self._client.get_status()

    def is_healthy(self) -> bool:
        """
        Quick health check for OpenViking system.

        Performs a quick check to determine if all OpenViking components
        are operating normally.

        Returns:
            bool: True if all components are healthy, False otherwise.

        Example:
            >>> if client.is_healthy():
            ...     print("OpenViking is healthy!")
            ... else:
            ...     print("OpenViking has issues - check get_status()")

        Note:
            This method will auto-initialize the client if not already initialized.

        See Also:
            - get_status(): Detailed system status
        """
        return self._client.is_healthy()

    @property
    def observer(self):
        """Get observer service for component status."""
        return self._client.observer
