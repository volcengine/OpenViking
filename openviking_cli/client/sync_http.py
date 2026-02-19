# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Synchronous HTTP Client for OpenViking.

Wraps AsyncHTTPClient with synchronous methods.
"""

from typing import Any, Dict, List, Optional, Union

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils import run_async


class SyncHTTPClient:
    """Synchronous HTTP Client for OpenViking Server.

    Wraps AsyncHTTPClient with synchronous methods.
    Supports auto-loading url/api_key from ovcli.conf when not provided.

    Examples:
        # Explicit url
        client = SyncHTTPClient(url="http://localhost:1933", api_key="key")
        client.initialize()

        # Auto-load from ~/.openviking/ovcli.conf
        client = SyncHTTPClient()
        client.initialize()
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._async_client = AsyncHTTPClient(url=url, api_key=api_key)
        self._initialized = False

    # ============= Lifecycle =============

    def initialize(self) -> None:
        """Initialize the HTTP client."""
        run_async(self._async_client.initialize())
        self._initialized = True

    def close(self) -> None:
        """Close the HTTP client and release resources."""
        run_async(self._async_client.close())
        self._initialized = False

    # ============= session =============

    def session(self, session_id: Optional[str] = None) -> Any:
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

    # ============= Resource =============

    def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking."""
        return run_async(
            self._async_client.add_resource(path, target, reason, instruction, wait, timeout)
        )

    def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        return run_async(self._async_client.add_skill(data, wait=wait, timeout=timeout))

    def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all processing to complete."""
        return run_async(self._async_client.wait_processed(timeout))

    # ============= Search =============

    def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional[Any] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ):
        """Semantic search with optional session context."""
        return run_async(
            self._async_client.search(
                query=query,
                target_uri=target_uri,
                session=session,
                session_id=session_id,
                limit=limit,
                score_threshold=score_threshold,
                filter=filter,
            )
        )

    def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ):
        """Semantic search without session context."""
        return run_async(self._async_client.find(query, target_uri, limit, score_threshold, filter))

    def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search with pattern."""
        return run_async(self._async_client.grep(uri, pattern, case_insensitive))

    def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching."""
        return run_async(self._async_client.glob(pattern, uri))

    # ============= File System =============

    def ls(
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
        return run_async(
            self._async_client.ls(
                uri,
                simple=simple,
                recursive=recursive,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
            )
        )

    def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        return run_async(
            self._async_client.tree(
                uri,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
            )
        )

    def stat(self, uri: str) -> Dict:
        """Get resource status."""
        return run_async(self._async_client.stat(uri))

    def mkdir(self, uri: str) -> None:
        """Create directory."""
        run_async(self._async_client.mkdir(uri))

    def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource."""
        run_async(self._async_client.rm(uri, recursive))

    def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource."""
        run_async(self._async_client.mv(from_uri, to_uri))

    # ============= Content =============

    def read(self, uri: str) -> str:
        """Read file content."""
        return run_async(self._async_client.read(uri))

    def abstract(self, uri: str) -> str:
        """Read L0 abstract."""
        return run_async(self._async_client.abstract(uri))

    def overview(self, uri: str) -> str:
        """Read L1 overview."""
        return run_async(self._async_client.overview(uri))

    # ============= Relations =============

    def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations for a resource."""
        return run_async(self._async_client.relations(uri))

    def link(self, from_uri: str, uris: Union[str, List[str]], reason: str = "") -> None:
        """Create link between resources."""
        run_async(self._async_client.link(from_uri, uris, reason))

    def unlink(self, from_uri: str, uri: str) -> None:
        """Remove link between resources."""
        run_async(self._async_client.unlink(from_uri, uri))

    # ============= Pack =============

    def export_ovpack(self, uri: str, to: str) -> str:
        """Export context as .ovpack file."""
        return run_async(self._async_client.export_ovpack(uri, to))

    def import_ovpack(
        self, file_path: str, target: str, force: bool = False, vectorize: bool = True
    ) -> str:
        """Import .ovpack file."""
        return run_async(self._async_client.import_ovpack(file_path, target, force, vectorize))

    # ============= Debug =============

    def health(self) -> bool:
        """Check server health."""
        return run_async(self._async_client.health())

    def get_status(self) -> Dict[str, Any]:
        """Get system status."""
        return self._async_client.get_status()

    def is_healthy(self) -> bool:
        """Quick health check."""
        return self._async_client.is_healthy()

    @property
    def observer(self):
        """Get observer service for component status."""
        return self._async_client.observer
