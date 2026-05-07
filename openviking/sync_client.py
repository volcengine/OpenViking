# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Synchronous OpenViking client implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    from openviking.session import Session

from openviking.async_client import AsyncOpenViking
from openviking.telemetry import TelemetryRequest
from openviking_cli.utils import run_async


class SyncOpenViking:
    """
    SyncOpenViking main client class (Synchronous).
    Wraps AsyncOpenViking with synchronous methods.
    """

    def __init__(self, **kwargs):
        self._async_client = AsyncOpenViking(**kwargs)
        self._initialized = False

    def initialize(self) -> None:
        """Initialize OpenViking storage and indexes."""
        run_async(self._async_client.initialize())
        self._initialized = True

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> "Session":
        """Create new session or load existing session."""
        return self._async_client.session(session_id, must_exist=must_exist)

    def session_exists(self, session_id: str) -> bool:
        """Check whether a session exists in storage."""
        return run_async(self._async_client.session_exists(session_id))

    def create_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new session.

        Args:
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.
        """
        return run_async(self._async_client.create_session(session_id))

    def list_sessions(self) -> List[Any]:
        """List all sessions."""
        return run_async(self._async_client.list_sessions())

    def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        """Get session details."""
        return run_async(self._async_client.get_session(session_id, auto_create=auto_create))

    def get_session_context(self, session_id: str, token_budget: int = 128_000) -> Dict[str, Any]:
        """Get assembled session context."""
        return run_async(
            self._async_client.get_session_context(session_id, token_budget=token_budget)
        )

    def get_session_archive(self, session_id: str, archive_id: str) -> Dict[str, Any]:
        """Get one completed archive for a session."""
        return run_async(self._async_client.get_session_archive(session_id, archive_id))

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        run_async(self._async_client.delete_session(session_id))

    def add_message(
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
            created_at: Message creation time (ISO format string). If not provided, current time is used.
            role_id: Optional explicit actor identity. Omit to let the client/server derive it.

        If both content and parts are provided, parts takes precedence.
        """
        return run_async(
            self._async_client.add_message(session_id, role, content, parts, created_at, role_id)
        )

    def commit_session(
        self, session_id: str, telemetry: TelemetryRequest = False
    ) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
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
        """Add resource to OpenViking (resources scope only)

        Args:
            build_index: Whether to build vector index immediately (default: True).
            summarize: Whether to generate summary (default: False).
            **kwargs: Extra options forwarded to the parser chain, e.g.
                ``strict``, ``ignore_dirs``, ``include``, ``exclude``.
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
        target_uri: Union[str, List[str]] = "",
        session: Optional["Session"] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
    ):
        """Execute complex retrieval (intent analysis, hierarchical retrieval)."""
        return run_async(
            self._async_client.search(
                query=query,
                target_uri=target_uri,
                session=session,
                session_id=session_id,
                limit=limit,
                score_threshold=score_threshold,
                filter=filter,
                telemetry=telemetry,
                since=since,
                until=until,
                time_field=time_field,
            )
        )

    def find(
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
        """Quick retrieval"""
        return run_async(
            self._async_client.find(
                query,
                target_uri,
                limit,
                score_threshold,
                filter,
                telemetry,
                since,
                until,
                time_field,
            )
        )

    def abstract(self, uri: str) -> str:
        """Read L0 abstract"""
        return run_async(self._async_client.abstract(uri))

    def overview(self, uri: str) -> str:
        """Read L1 overview"""
        return run_async(self._async_client.overview(uri))

    def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        """Read file"""
        return run_async(self._async_client.read(uri, offset=offset, limit=limit))

    def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Write text content to an existing file and refresh semantics/vectors."""
        return run_async(
            self._async_client.write(
                uri=uri,
                content=content,
                mode=mode,
                wait=wait,
                timeout=timeout,
                telemetry=telemetry,
            )
        )

    def ls(self, uri: str, **kwargs) -> List[Any]:
        """
        List directory contents.

        Args:
            uri: Viking URI
            simple: Return only relative path list (bool, default: False)
            recursive: List all subdirectories recursively (bool, default: False)
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
        """Close OpenViking and release resources."""
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

    def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
    ) -> Dict:
        """Content search"""
        return run_async(
            self._async_client.grep(uri, pattern, case_insensitive, node_limit, exclude_uri)
        )

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

    def mkdir(self, uri: str, description: Optional[str] = None) -> None:
        """Create directory"""
        return run_async(self._async_client.mkdir(uri, description=description))

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
