# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Synchronous OpenViking client implementation.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from openviking.session import Session

from openviking.async_client import AsyncOpenViking
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

    def session(self, session_id: Optional[str] = None) -> "Session":
        """Create new session or load existing session."""
        return self._async_client.session(session_id)

    def create_session(self) -> Dict[str, Any]:
        """Create a new session."""
        return run_async(self._async_client.create_session())

    def list_sessions(self) -> List[Any]:
        """List all sessions."""
        return run_async(self._async_client.list_sessions())

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session details."""
        return run_async(self._async_client.get_session(session_id))

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        run_async(self._async_client.delete_session(session_id))

    def add_message(self, session_id: str, role: str, content: str) -> Dict[str, Any]:
        """Add a message to a session."""
        return run_async(self._async_client.add_message(session_id, role, content))

    def commit_session(self, session_id: str) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        return run_async(self._async_client.commit_session(session_id))

    def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: float = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (resources scope only)

        Args:
            **kwargs: Extra options forwarded to the parser chain, e.g.
                ``strict``, ``ignore_dirs``, ``include``, ``exclude``.
        """
        return run_async(
            self._async_client.add_resource(
                path,
                target,
                reason,
                instruction,
                wait,
                timeout,
                **kwargs,
            )
        )

    def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: float = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        return run_async(self._async_client.add_skill(data, wait=wait, timeout=timeout))

    def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional["Session"] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ):
        """Execute complex retrieval (intent analysis, hierarchical retrieval)."""
        return run_async(
            self._async_client.search(
                query, target_uri, session, session_id, limit, score_threshold, filter
            )
        )

    def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
    ):
        """Quick retrieval"""
        return run_async(self._async_client.find(query, target_uri, limit, score_threshold))

    def abstract(self, uri: str) -> str:
        """Read L0 abstract"""
        return run_async(self._async_client.abstract(uri))

    def overview(self, uri: str) -> str:
        """Read L1 overview"""
        return run_async(self._async_client.overview(uri))

    def read(self, uri: str) -> str:
        """Read file"""
        return run_async(self._async_client.read(uri))

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
