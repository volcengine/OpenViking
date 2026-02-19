# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Base client interface for OpenViking.

Defines the abstract base class that both LocalClient and AsyncHTTPClient implement.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


class BaseClient(ABC):
    """Abstract base class for OpenViking clients.

    Both LocalClient (embedded mode) and AsyncHTTPClient (HTTP mode) implement this interface.
    """

    # ============= Lifecycle =============

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the client."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the client and release resources."""
        ...

    # ============= Resource Management =============

    @abstractmethod
    async def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking."""
        ...

    @abstractmethod
    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        ...

    @abstractmethod
    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all processing to complete."""
        ...

    # ============= File System =============

    @abstractmethod
    async def ls(
        self,
        uri: str,
        simple: bool = False,
        recursive: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Any]:
        """List directory contents."""
        ...

    @abstractmethod
    async def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        ...

    @abstractmethod
    async def stat(self, uri: str) -> Dict[str, Any]:
        """Get resource status."""
        ...

    @abstractmethod
    async def mkdir(self, uri: str) -> None:
        """Create directory."""
        ...

    @abstractmethod
    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource."""
        ...

    @abstractmethod
    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource."""
        ...

    # ============= Content Reading =============

    @abstractmethod
    async def read(self, uri: str) -> str:
        """Read file content (L2)."""
        ...

    @abstractmethod
    async def abstract(self, uri: str) -> str:
        """Read L0 abstract (.abstract.md)."""
        ...

    @abstractmethod
    async def overview(self, uri: str) -> str:
        """Read L1 overview (.overview.md)."""
        ...

    # ============= Search =============

    @abstractmethod
    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ) -> Any:
        """Semantic search without session context."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        target_uri: str = "",
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ) -> Any:
        """Semantic search with optional session context."""
        ...

    @abstractmethod
    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict[str, Any]:
        """Content search with pattern."""
        ...

    @abstractmethod
    async def glob(self, pattern: str, uri: str = "viking://") -> Dict[str, Any]:
        """File pattern matching."""
        ...

    # ============= Relations =============

    @abstractmethod
    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations for a resource."""
        ...

    @abstractmethod
    async def link(self, from_uri: str, to_uris: Union[str, List[str]], reason: str = "") -> None:
        """Create link between resources."""
        ...

    @abstractmethod
    async def unlink(self, from_uri: str, to_uri: str) -> None:
        """Remove link between resources."""
        ...

    # ============= Sessions =============

    @abstractmethod
    async def create_session(self) -> Dict[str, Any]:
        """Create a new session."""
        ...

    @abstractmethod
    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions."""
        ...

    @abstractmethod
    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session details."""
        ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        ...

    @abstractmethod
    async def commit_session(self, session_id: str) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        ...

    @abstractmethod
    async def add_message(self, session_id: str, role: str, content: str) -> Dict[str, Any]:
        """Add a message to a session."""
        ...

    # ============= Pack =============

    @abstractmethod
    async def export_ovpack(self, uri: str, to: str) -> str:
        """Export as .ovpack file."""
        ...

    @abstractmethod
    async def import_ovpack(
        self, file_path: str, parent: str, force: bool = False, vectorize: bool = True
    ) -> str:
        """Import .ovpack file."""
        ...

    # ============= Debug =============

    @abstractmethod
    async def health(self) -> bool:
        """Quick health check."""
        ...

    @abstractmethod
    def session(self, session_id: Optional[str] = None) -> Any:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session if None

        Returns:
            Session object
        """
        ...

    @abstractmethod
    def get_status(self) -> Any:
        """Get system status.

        Returns:
            SystemStatus or Dict containing health status of all components.
        """
        ...

    @abstractmethod
    def is_healthy(self) -> bool:
        """Quick health check (synchronous).

        Returns:
            True if all components are healthy, False otherwise.
        """
        ...

    @property
    @abstractmethod
    def observer(self) -> Any:
        """Get observer service for component status."""
        ...
