# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Async OpenViking client implementation (embedded mode only).

For HTTP mode, use AsyncHTTPClient or SyncHTTPClient.
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
    OpenViking main client class (Asynchronous, embedded mode only).

    Uses local storage and auto-starts services (singleton).
    For HTTP mode, use AsyncHTTPClient or SyncHTTPClient instead.

    Examples:
        client = AsyncOpenViking(path="./data")
        await client.initialize()
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
        Initialize OpenViking client (embedded mode).

        Args:
            path: Local storage path (overrides ov.conf storage path).
            **kwargs: Additional configuration parameters.
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
        """Initialize OpenViking storage and indexes."""
        await self._client.initialize()
        self._initialized = True

    async def _ensure_initialized(self):
        """Ensure storage collections are initialized."""
        if not self._initialized:
            await self.initialize()

    async def close(self) -> None:
        """Close OpenViking and release resources."""
        client = getattr(self, "_client", None)
        if client is not None:
            await client.close()
        self._initialized = False
        self._singleton_initialized = False

    @classmethod
    async def reset(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
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

        Args:
            session_id: Session ID, creates a new session (auto-generated ID) if None
            must_exist: If True and session_id is provided, raises NotFoundError
                        when the session does not exist.
                        If session_id is None, must_exist is ignored.
        """
        return self._client.session(session_id, must_exist=must_exist)

    async def session_exists(self, session_id: str) -> bool:
        """Check whether a session exists in storage.

        Args:
            session_id: Session ID to check

        Returns:
            True if the session exists, False otherwise
        """
        await self._ensure_initialized()
        return await self._client.session_exists(session_id)

    async def create_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new session.

        Args:
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.
        """
        await self._ensure_initialized()
        return await self._client.create_session(session_id)

    async def list_sessions(self) -> List[Any]:
        """List all sessions."""
        await self._ensure_initialized()
        return await self._client.list_sessions()

    async def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        """Get session details."""
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
        """Delete a session."""
        await self._ensure_initialized()
        await self._client.delete_session(session_id)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
        role_id: str | None = None,
    ) -> Dict[str, Any]:
        """Add a message to a session.

        Args:
            session_id: Session ID
            role: Message role ("user" or "assistant")
            content: Text content (simple mode)
            parts: Parts array (full Part support: TextPart, ContextPart, ToolPart)
            created_at: Message creation time (ISO format string)
            role_id: Optional explicit actor identity. Omit to let the client/server derive it.

        If both content and parts are provided, parts takes precedence.
        """
        await self._ensure_initialized()
        return await self._client.add_message(
            session_id=session_id,
            role=role,
            content=content,
            parts=parts,
            created_at=created_at,
            role_id=role_id,
        )

    async def commit_session(
        self, session_id: str, telemetry: TelemetryRequest = False
    ) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
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
        Add a resource (file/URL) to OpenViking.

        Args:
            path: Local file path or URL.
            reason: Context/reason for adding this resource.
            instruction: Specific instruction for processing.
            wait: If True, wait for processing to complete.
            to: Exact target URI (must not exist yet).
            parent: Target parent URI (must already exist).
            build_index: Whether to build vector index immediately (default: True).
            summarize: Whether to generate summary (default: False).
            telemetry: Whether to attach operation telemetry data to the result.
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
        target_uri: Union[str, List[str]] = "",
        session: Optional[Union["Session", Any]] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
    ):
        """
        Complex search with session context.

        Args:
            query: Query string
            target_uri: Target directory URI
            session: Session object for context
            session_id: Session ID string (alternative to session object)
            limit: Max results
            filter: Metadata filters

        Returns:
            FindResult
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
            since=since,
            until=until,
            time_field=time_field,
        )

    async def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
    ):
        """Semantic search"""
        await self._ensure_initialized()
        return await self._client.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
            telemetry=telemetry,
            since=since,
            until=until,
            time_field=time_field,
        )

    # ============= FS methods =============

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract (.abstract.md)"""
        await self._ensure_initialized()
        return await self._client.abstract(uri)

    async def overview(self, uri: str) -> str:
        """Read L1 overview (.overview.md)"""
        await self._ensure_initialized()
        return await self._client.overview(uri)

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        """Read file content"""
        await self._ensure_initialized()
        return await self._client.read(uri, offset=offset, limit=limit)

    async def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Write text content to an existing file and refresh semantics/vectors."""
        await self._ensure_initialized()
        return await self._client.write(
            uri=uri,
            content=content,
            mode=mode,
            wait=wait,
            timeout=timeout,
            telemetry=telemetry,
        )

    async def ls(self, uri: str, **kwargs) -> List[Any]:
        """
        List directory contents.

        Args:
            uri: Viking URI
            simple: Return only relative path list (bool, default: False)
            recursive: List all subdirectories recursively (bool, default: False)
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
        """Remove resource"""
        await self._ensure_initialized()
        await self._client.rm(uri, recursive=recursive)

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
    ) -> Dict:
        """Content search"""
        await self._ensure_initialized()
        return await self._client.grep(
            uri,
            pattern,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            exclude_uri=exclude_uri,
        )

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching"""
        await self._ensure_initialized()
        return await self._client.glob(pattern, uri=uri)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource"""
        await self._ensure_initialized()
        await self._client.mv(from_uri, to_uri)

    async def tree(self, uri: str, **kwargs) -> Dict:
        """Get directory tree"""
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

    async def mkdir(self, uri: str, description: Optional[str] = None) -> None:
        """Create directory"""
        await self._ensure_initialized()
        await self._client.mkdir(uri, description=description)

    async def stat(self, uri: str) -> Dict:
        """Get resource status"""
        await self._ensure_initialized()
        return await self._client.stat(uri)

    # ============= Relation methods =============

    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations (returns [{"uri": "...", "reason": "..."}, ...])"""
        await self._ensure_initialized()
        return await self._client.relations(uri)

    async def link(self, from_uri: str, uris: Any, reason: str = "") -> None:
        """
        Create link (single or multiple).

        Args:
            from_uri: Source URI
            uris: Target URI or list of URIs
            reason: Reason for linking
        """
        await self._ensure_initialized()
        await self._client.link(from_uri, uris, reason)

    async def unlink(self, from_uri: str, uri: str) -> None:
        """
        Remove link (remove specified URI from uris).

        Args:
            from_uri: Source URI
            uri: Target URI to remove
        """
        await self._ensure_initialized()
        await self._client.unlink(from_uri, uri)

    # ============= Pack methods =============

    async def export_ovpack(self, uri: str, to: str) -> str:
        """
        Export specified context path as .ovpack file.

        Args:
            uri: Viking URI
            to: Target file path

        Returns:
            Exported file path
        """
        await self._ensure_initialized()
        return await self._client.export_ovpack(uri, to)

    async def import_ovpack(
        self, file_path: str, parent: str, force: bool = False, vectorize: bool = True
    ) -> str:
        """
        Import local .ovpack file to specified parent path.

        Args:
            file_path: Local .ovpack file path
            parent: Target parent URI (e.g., viking://user/alice/resources/references/)
            force: Whether to force overwrite existing resources (default: False)
            vectorize: Whether to trigger vectorization (default: True)

        Returns:
            Imported root resource URI
        """
        await self._ensure_initialized()
        return await self._client.import_ovpack(file_path, parent, force=force, vectorize=vectorize)

    # ============= Debug methods =============

    def get_status(self) -> Union[SystemStatus, Dict[str, Any]]:
        """Get system status.

        Returns:
            SystemStatus containing health status of all components.
        """
        return self._client.get_status()

    def is_healthy(self) -> bool:
        """Quick health check.

        Returns:
            True if all components are healthy, False otherwise.
        """
        return self._client.is_healthy()

    @property
    def observer(self):
        """Get observer service for component status."""
        return self._client.observer
