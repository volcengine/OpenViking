"""
Memex Client - OpenViking client wrapper for Memex.
"""

from typing import Any, Optional

import openviking as ov

from .config import MemexConfig


class MemexClient:
    """OpenViking client wrapper for Memex."""

    def __init__(self, config: Optional[MemexConfig] = None):
        """Initialize Memex client.

        Args:
            config: Memex configuration. If None, uses default config from env.
        """
        self.config = config or MemexConfig.from_env()
        self._client: Optional[ov.SyncOpenViking] = None
        self._session = None

    @property
    def client(self) -> ov.SyncOpenViking:
        """Get the OpenViking client instance."""
        if self._client is None:
            raise RuntimeError("Client not initialized. Call initialize() first.")
        return self._client

    def initialize(self) -> None:
        """Initialize the OpenViking client."""
        ov_config = self.config.get_openviking_config()
        self._client = ov.SyncOpenViking(
            path=self.config.data_path,
            config=ov_config,
        )
        self._client.initialize()

    def close(self) -> None:
        """Close the client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ==================== Resource Management ====================

    def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a resource to the knowledge base.

        Args:
            path: File path, directory path, or URL.
            target: Target URI in viking://. Defaults to viking://resources/.
            reason: Reason for adding this resource.
            instruction: Special instructions for processing.

        Returns:
            Dict with root_uri and other metadata.
        """
        target = target or self.config.default_resource_uri
        return self.client.add_resource(
            path=path,
            target=target,
            reason=reason,
            instruction=instruction,
        )

    def remove(self, uri: str, recursive: bool = False) -> None:
        """Remove a resource from the knowledge base.

        Args:
            uri: URI of the resource to remove.
            recursive: Whether to remove recursively.
        """
        self.client.rm(uri=uri, recursive=recursive)

    def wait_processed(self, timeout: Optional[float] = None) -> None:
        """Wait for all pending resources to be processed.

        Args:
            timeout: Maximum time to wait in seconds.
        """
        self.client.wait_processed(timeout=timeout)

    # ==================== File System Operations ====================

    def ls(self, uri: Optional[str] = None) -> list[dict[str, Any]]:
        """List contents of a directory.

        Args:
            uri: URI to list. Defaults to viking://resources/.

        Returns:
            List of items in the directory.
        """
        uri = uri or self.config.default_resource_uri
        return self.client.ls(uri=uri)

    def tree(self, uri: Optional[str] = None) -> str:
        """Get directory tree as string.

        Args:
            uri: URI to get tree for. Defaults to viking://resources/.

        Returns:
            Tree structure as string.
        """
        uri = uri or self.config.default_resource_uri
        return self.client.tree(uri=uri)

    def read(self, uri: str) -> str:
        """Read full content of a resource (L2).

        Args:
            uri: URI of the resource.

        Returns:
            Full content of the resource.
        """
        return self.client.read(uri=uri)

    def abstract(self, uri: str) -> str:
        """Get abstract/summary of a resource (L0).

        Args:
            uri: URI of the resource.

        Returns:
            Abstract/summary of the resource.
        """
        return self.client.abstract(uri=uri)

    def overview(self, uri: str) -> str:
        """Get overview of a resource (L1).

        Args:
            uri: URI of the resource.

        Returns:
            Overview of the resource.
        """
        return self.client.overview(uri=uri)

    def glob(self, pattern: str, uri: Optional[str] = None) -> list[str]:
        """Find resources matching a pattern.

        Args:
            pattern: Glob pattern to match.
            uri: Base URI to search in.

        Returns:
            List of matching URIs.
        """
        uri = uri or self.config.default_resource_uri
        return self.client.glob(pattern=pattern, uri=uri)

    def grep(self, uri: str, pattern: str) -> list[dict[str, Any]]:
        """Search content within resources.

        Args:
            uri: URI to search in.
            pattern: Pattern to search for.

        Returns:
            List of matches.
        """
        return self.client.grep(uri=uri, pattern=pattern)

    def stat(self, uri: str) -> dict[str, Any]:
        """Get metadata about a resource.

        Args:
            uri: URI of the resource.

        Returns:
            Resource metadata.
        """
        return self.client.stat(uri=uri)

    # ==================== Search ====================

    def find(
        self,
        query: str,
        target_uri: Optional[str] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> Any:
        """Quick semantic search.

        Args:
            query: Search query.
            target_uri: URI to search in.
            top_k: Number of results to return.
            score_threshold: Minimum score threshold.

        Returns:
            Search results.
        """
        target_uri = target_uri or self.config.default_resource_uri
        limit = top_k or self.config.search_top_k
        score_threshold = score_threshold or self.config.search_score_threshold
        return self.client.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
        )

    def search(
        self,
        query: str,
        target_uri: Optional[str] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        session: Optional[Any] = None,
    ) -> Any:
        """Deep semantic search with intent analysis.

        Args:
            query: Search query.
            target_uri: URI to search in.
            top_k: Number of results to return.
            score_threshold: Minimum score threshold.
            session: OpenViking Session for context-aware search.

        Returns:
            Search results.
        """
        target_uri = target_uri or self.config.default_resource_uri
        limit = top_k or self.config.search_top_k
        score_threshold = score_threshold or self.config.search_score_threshold
        return self.client.search(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            session=session,
        )

    # ==================== Session Management ====================

    def get_session(self, session_id: Optional[str] = None):
        """Get or create a session.

        Args:
            session_id: Session ID. If None, uses config session_id or generates one.

        Returns:
            Session object.
        """
        session_id = session_id or self.config.session_id
        if session_id:
            self._session = self.client.session(session_id=session_id)
        else:
            self._session = self.client.session()
        return self._session

    @property
    def session(self):
        """Get current session."""
        return self._session

    # ==================== Statistics ====================

    def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics.

        Returns:
            Statistics about the knowledge base.
        """
        stats = {
            "resources": {"count": 0, "types": {}},
            "user": {"memories": 0},
            "agent": {"skills": 0, "memories": 0},
        }

        # Count resources
        try:
            resources = self.ls(self.config.default_resource_uri)
            stats["resources"]["count"] = len(resources)
            for item in resources:
                item_type = item.get("type", "unknown")
                stats["resources"]["types"][item_type] = (
                    stats["resources"]["types"].get(item_type, 0) + 1
                )
        except Exception:
            pass

        # Count user memories
        try:
            user_memories = self.ls(f"{self.config.default_user_uri}memories/")
            stats["user"]["memories"] = len(user_memories)
        except Exception:
            pass

        # Count agent skills and memories
        try:
            agent_skills = self.ls(f"{self.config.default_agent_uri}skills/")
            stats["agent"]["skills"] = len(agent_skills)
        except Exception:
            pass

        try:
            agent_memories = self.ls(f"{self.config.default_agent_uri}memories/")
            stats["agent"]["memories"] = len(agent_memories)
        except Exception:
            pass

        return stats
