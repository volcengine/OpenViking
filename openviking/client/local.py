# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Local Client for OpenViking.

Implements BaseClient interface using direct service calls (embedded mode).
"""

from typing import Any, Dict, List, Optional, Union

from openviking.service import OpenVikingService
from openviking_cli.client.base import BaseClient
from openviking_cli.session.user_id import UserIdentifier


class LocalClient(BaseClient):
    """Local Client for OpenViking (embedded mode).

    Implements BaseClient interface using direct service calls.
    """

    def __init__(
        self,
        path: Optional[str] = None,
    ):
        """Initialize LocalClient.

        Args:
            path: Local storage path (overrides ov.conf storage path)
        """
        self._service = OpenVikingService(
            path=path,
            user=UserIdentifier.the_default_user(),
        )
        self._user = self._service.user

    @property
    def service(self) -> OpenVikingService:
        """Get the underlying service instance."""
        return self._service

    # ============= Lifecycle =============

    async def initialize(self) -> None:
        """Initialize the local client."""
        await self._service.initialize()

    async def close(self) -> None:
        """Close the local client."""
        await self._service.close()

    # ============= Resource Management =============

    async def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking."""
        return await self._service.resources.add_resource(
            path=path,
            target=target,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout,
            **kwargs,
        )

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        return await self._service.resources.add_skill(
            data=data,
            wait=wait,
            timeout=timeout,
        )

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all processing to complete."""
        return await self._service.resources.wait_processed(timeout=timeout)

    # ============= File System =============

    async def ls(
        self,
        uri: str,
        simple: bool = False,
        recursive: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
    ) -> List[Any]:
        """List directory contents."""
        return await self._service.fs.ls(
            uri,
            simple=simple,
            recursive=recursive,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
        )

    async def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        return await self._service.fs.tree(
            uri,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
        )

    async def stat(self, uri: str) -> Dict[str, Any]:
        """Get resource status."""
        return await self._service.fs.stat(uri)

    async def mkdir(self, uri: str) -> None:
        """Create directory."""
        await self._service.fs.mkdir(uri)

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource."""
        await self._service.fs.rm(uri, recursive=recursive)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource."""
        await self._service.fs.mv(from_uri, to_uri)

    # ============= Content Reading =============

    async def read(self, uri: str) -> str:
        """Read file content."""
        return await self._service.fs.read(uri)

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract."""
        return await self._service.fs.abstract(uri)

    async def overview(self, uri: str) -> str:
        """Read L1 overview."""
        return await self._service.fs.overview(uri)

    # ============= Search =============

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Semantic search without session context."""
        return await self._service.search.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
        )

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Semantic search with optional session context."""
        session = None
        if session_id:
            session = self._service.sessions.session(session_id)
            session.load()
        return await self._service.search.search(
            query=query,
            target_uri=target_uri,
            session=session,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
        )

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict[str, Any]:
        """Content search with pattern."""
        return await self._service.fs.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict[str, Any]:
        """File pattern matching."""
        return await self._service.fs.glob(pattern, uri=uri)

    # ============= Relations =============

    async def relations(self, uri: str) -> List[Any]:
        """Get relations for a resource."""
        return await self._service.relations.relations(uri)

    async def link(self, from_uri: str, to_uris: Union[str, List[str]], reason: str = "") -> None:
        """Create link between resources."""
        await self._service.relations.link(from_uri, to_uris, reason)

    async def unlink(self, from_uri: str, to_uri: str) -> None:
        """Remove link between resources."""
        await self._service.relations.unlink(from_uri, to_uri)

    # ============= Sessions =============

    async def create_session(self) -> Dict[str, Any]:
        """Create a new session."""
        session = self._service.sessions.session()
        return {
            "session_id": session.session_id,
            "user": session.user.to_dict(),
        }

    async def list_sessions(self) -> List[Any]:
        """List all sessions."""
        return await self._service.sessions.sessions()

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session details."""
        session = self._service.sessions.session(session_id)
        session.load()
        return {
            "session_id": session.session_id,
            "user": session.user.to_dict(),
            "message_count": len(session.messages),
        }

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        await self._service.sessions.delete(session_id)

    async def commit_session(self, session_id: str) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        return await self._service.sessions.commit(session_id)

    async def add_message(self, session_id: str, role: str, content: str) -> Dict[str, Any]:
        """Add a message to a session."""
        session = self._service.sessions.session(session_id)
        session.load()
        session.add_message(role, content)
        return {
            "session_id": session_id,
            "message_count": len(session.messages),
        }

    # ============= Pack =============

    async def export_ovpack(self, uri: str, to: str) -> str:
        """Export context as .ovpack file."""
        return await self._service.pack.export_ovpack(uri, to)

    async def import_ovpack(
        self,
        file_path: str,
        parent: str,
        force: bool = False,
        vectorize: bool = True,
    ) -> str:
        """Import .ovpack file."""
        return await self._service.pack.import_ovpack(
            file_path, parent, force=force, vectorize=vectorize
        )

    # ============= Debug =============

    async def health(self) -> bool:
        """Check service health."""
        return True  # Local service is always healthy if initialized

    def session(self, session_id: Optional[str] = None) -> Any:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session if None

        Returns:
            Session object
        """
        from openviking.session import Session

        return Session(
            viking_fs=self._service.viking_fs,
            vikingdb_manager=self._service.vikingdb_manager,
            session_compressor=self._service.session_compressor,
            user=self._user,
            session_id=session_id,
        )

    def get_status(self) -> Any:
        """Get system status.

        Returns:
            SystemStatus containing health status of all components.
        """
        return self._service.debug.observer.system

    def is_healthy(self) -> bool:
        """Quick health check (synchronous).

        Returns:
            True if all components are healthy, False otherwise.
        """
        return self._service.debug.observer.is_healthy()

    @property
    def observer(self) -> Any:
        """Get observer service for component status."""
        return self._service.debug.observer
