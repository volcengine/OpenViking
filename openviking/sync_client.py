# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Synchronous OpenViking client implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    from openviking.session import Session
    from openviking.snapshot_namespace import SyncSnapshotNamespace

from openviking.async_client import AsyncOpenViking
from openviking.telemetry import TelemetryRequest
from openviking.utils.search_filters import SearchContextTypeInput
from openviking_cli.utils import run_async


class SyncOpenViking:
    """
    SyncOpenViking main client class (Synchronous).
    Wraps AsyncOpenViking with synchronous methods.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        actor_peer_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        self._async_client = AsyncOpenViking(
            path=path,
            actor_peer_id=actor_peer_id,
            agent_id=agent_id,
        )
        self._initialized = False
        self._snapshot: Optional["SyncSnapshotNamespace"] = None

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

    def create_session(
        self,
        session_id: Optional[str] = None,
        telemetry: TelemetryRequest = False,
        memory_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new session.

        Args:
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.
        """
        return run_async(
            self._async_client.create_session(
                session_id,
                telemetry=telemetry,
                memory_policy=memory_policy,
            )
        )

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
        peer_id: str | None = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add a message to a session.

        Args:
            session_id: Session ID
            role: Message role ("user" or "assistant")
            content: Text content (simple mode)
            parts: Parts array (full Part support: TextPart, ContextPart, ImagePart, ToolPart)
            created_at: Message creation time (ISO format string). If not provided, current time is used.
            peer_id: Optional stable interaction peer identity.

        If both content and parts are provided, parts takes precedence.
        """
        return run_async(
            self._async_client.add_message(
                session_id=session_id,
                role=role,
                content=content,
                parts=parts,
                created_at=created_at,
                peer_id=peer_id,
                telemetry=telemetry,
            )
        )

    def batch_add_messages(
        self,
        session_id: str,
        messages: list[dict],
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add multiple messages to a session in a single request."""
        return run_async(
            self._async_client.batch_add_messages(
                session_id,
                messages,
                telemetry,
            )
        )

    def commit_session(
        self,
        session_id: str,
        telemetry: TelemetryRequest = False,
        *,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        return run_async(
            self._async_client.commit_session(
                session_id,
                telemetry=telemetry,
                keep_recent_count=keep_recent_count,
            )
        )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Query background task status."""
        return run_async(self._async_client.get_task(task_id))

    def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List background tasks visible to the current caller."""
        return run_async(
            self._async_client.list_tasks(
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                limit=limit,
            )
        )

    def reindex(
        self,
        uri: str,
        mode: str = "vectors_only",
        wait: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Reindex semantic/vector artifacts for a URI."""
        return run_async(
            self._async_client.reindex(
                uri=uri,
                mode=mode,
                wait=wait,
                dry_run=dry_run,
            )
        )

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
        args: Optional[Dict[str, Any]] = None,
        telemetry: TelemetryRequest = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (resources scope only)

        A sitemap / RSS / Atom URL ingests the whole site as one resource tree;
        pass ``args={"site": True}`` to force whole-site ingestion from a bare
        domain. A ``watch_interval`` on a sitemap/feed URL keeps the whole site
        refreshed.

        Args:
            to: Exact target URI. Existing targets keep the add_resource incremental-update behavior.
            parent: Target parent URI for automatic child naming.
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
                args=args,
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
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        return run_async(
            self._async_client.add_skill(
                data,
                wait=wait,
                timeout=timeout,
                telemetry=telemetry,
                target_uri=target_uri,
            )
        )

    def list_skills(
        self,
        node_limit: int = 1000,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List installed skills."""
        return run_async(
            self._async_client.list_skills(
                node_limit=node_limit,
                target_uri=target_uri,
            )
        )

    def find_skills(
        self,
        query: str,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        level: Optional[List[int]] = None,
        telemetry: TelemetryRequest = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find skills by semantic search."""
        return run_async(
            self._async_client.find_skills(
                query=query,
                limit=limit,
                score_threshold=score_threshold,
                level=level,
                telemetry=telemetry,
                target_uri=target_uri,
            )
        )

    def get_skill(
        self,
        skill_name: str,
        include_content: Optional[bool] = None,
        include_files: bool = True,
        include_source: bool = False,
        level: Optional[int] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get a skill by name."""
        return run_async(
            self._async_client.get_skill(
                skill_name=skill_name,
                include_content=include_content,
                include_files=include_files,
                include_source=include_source,
                level=level,
                target_uri=target_uri,
            )
        )

    def update_skill(
        self,
        skill_name: str,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
        telemetry: TelemetryRequest = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing skill."""
        return run_async(
            self._async_client.update_skill(
                skill_name=skill_name,
                data=data,
                wait=wait,
                timeout=timeout,
                source_metadata=source_metadata,
                telemetry=telemetry,
                target_uri=target_uri,
            )
        )

    def delete_skill(
        self,
        skill_name: str,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a skill."""
        return run_async(
            self._async_client.delete_skill(
                skill_name=skill_name,
                target_uri=target_uri,
            )
        )

    def validate_skill(
        self,
        data: Any,
        strict: bool = False,
        source_path: Optional[str] = None,
        skill_dir_name: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate skill data."""
        return run_async(
            self._async_client.validate_skill(
                data=data,
                strict=strict,
                source_path=source_path,
                skill_dir_name=skill_dir_name,
                target_uri=target_uri,
            )
        )

    def search(
        self,
        query: str = "",
        target_uri: Union[str, List[str]] = "",
        session: Optional["Session"] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        context_type: Optional[SearchContextTypeInput] = None,
        tags: Optional[List[str]] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
        level: Optional[List[int]] = None,
        image: Optional[Any] = None,
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
                context_type=context_type,
                tags=tags,
                telemetry=telemetry,
                since=since,
                until=until,
                time_field=time_field,
                level=level,
                image=image,
            )
        )

    def find(
        self,
        query: str = "",
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        context_type: Optional[SearchContextTypeInput] = None,
        tags: Optional[List[str]] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
        level: Optional[List[int]] = None,
        image: Optional[Any] = None,
    ):
        """Quick retrieval"""
        return run_async(
            self._async_client.find(
                query,
                target_uri,
                limit,
                score_threshold,
                filter,
                context_type,
                tags,
                telemetry,
                since,
                until,
                time_field,
                level,
                image,
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

    def set_tags(
        self,
        uri: str,
        tags: List[str],
        mode: str = "replace",
        recursive: bool = False,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Replace explicit retrieval tags for a file or directory."""
        return run_async(
            self._async_client.set_tags(
                uri=uri,
                tags=tags,
                mode=mode,
                recursive=recursive,
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

    def export_ovpack(self, uri: str, to: str, include_vectors: bool = False) -> str:
        """Export .ovpack file"""
        return run_async(self._async_client.export_ovpack(uri, to, include_vectors=include_vectors))

    def backup_ovpack(self, to: str, include_vectors: bool = False) -> str:
        """Back up public scopes as a restore-only .ovpack file."""
        return run_async(self._async_client.backup_ovpack(to, include_vectors=include_vectors))

    def import_ovpack(
        self,
        file_path: str,
        target: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        """Import .ovpack file (triggers vectorization by default)"""
        return run_async(
            self._async_client.import_ovpack(
                file_path,
                target,
                on_conflict=on_conflict,
                vector_mode=vector_mode,
            )
        )

    def restore_ovpack(
        self,
        file_path: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        """Restore backup .ovpack file."""
        return run_async(
            self._async_client.restore_ovpack(
                file_path,
                on_conflict=on_conflict,
                vector_mode=vector_mode,
            )
        )

    def check_consistency(self, uri: str) -> Dict[str, Any]:
        """Check filesystem/vector-index consistency for a URI subtree."""
        return run_async(self._async_client.check_consistency(uri))

    def close(self) -> None:
        """Close OpenViking and release resources."""
        return run_async(self._async_client.close())

    def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations"""
        return run_async(self._async_client.relations(uri))

    def rm(
        self,
        uri: str,
        recursive: bool = False,
        wait: bool = False,
        timeout: float = None,
    ) -> None:
        """Delete resource"""
        return run_async(self._async_client.rm(uri, recursive, wait=wait, timeout=timeout))

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
        level_limit: int = 5,
    ) -> Dict:
        """Content search"""
        return run_async(
            self._async_client.grep(
                uri,
                pattern,
                case_insensitive,
                node_limit,
                exclude_uri,
                level_limit,
            )
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

    @property
    def snapshot(self) -> "SyncSnapshotNamespace":
        """Snapshot version control namespace (synchronous)."""
        if getattr(self, "_snapshot", None) is None:
            from openviking.snapshot_namespace import SyncSnapshotNamespace

            self._snapshot = SyncSnapshotNamespace(self)
        return self._snapshot

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        return run_async(AsyncOpenViking.reset())
