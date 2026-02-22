# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Async HTTP Client for OpenViking.

Implements BaseClient interface using HTTP calls to OpenViking Server.
"""

from typing import Any, Dict, List, Optional, Union

import httpx

from openviking_cli.client.base import BaseClient
from openviking_cli.exceptions import (
    AlreadyExistsError,
    DeadlineExceededError,
    EmbeddingFailedError,
    InternalError,
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    NotInitializedError,
    OpenVikingError,
    PermissionDeniedError,
    ProcessingError,
    SessionExpiredError,
    UnauthenticatedError,
    UnavailableError,
    VLMFailedError,
)
from openviking_cli.retrieve.types import FindResult
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import run_async
from openviking_cli.utils.config.config_loader import (
    DEFAULT_OVCLI_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
    load_json_config,
    resolve_config_path,
)
from openviking_cli.utils.uri import VikingURI

# Error code to exception class mapping
ERROR_CODE_TO_EXCEPTION = {
    "INVALID_ARGUMENT": InvalidArgumentError,
    "INVALID_URI": InvalidURIError,
    "NOT_FOUND": NotFoundError,
    "ALREADY_EXISTS": AlreadyExistsError,
    "UNAUTHENTICATED": UnauthenticatedError,
    "PERMISSION_DENIED": PermissionDeniedError,
    "UNAVAILABLE": UnavailableError,
    "INTERNAL": InternalError,
    "DEADLINE_EXCEEDED": DeadlineExceededError,
    "NOT_INITIALIZED": NotInitializedError,
    "PROCESSING_ERROR": ProcessingError,
    "EMBEDDING_FAILED": EmbeddingFailedError,
    "VLM_FAILED": VLMFailedError,
    "SESSION_EXPIRED": SessionExpiredError,
}


class _HTTPObserver:
    """Observer proxy for HTTP mode.

    Provides the same interface as the local observer but fetches data via HTTP.
    """

    def __init__(self, client: "AsyncHTTPClient"):
        self._client = client
        self._cache = {}

    async def _fetch_queue_status(self) -> Dict[str, Any]:
        """Fetch queue status asynchronously."""
        return await self._client._get_queue_status()

    async def _fetch_vikingdb_status(self) -> Dict[str, Any]:
        """Fetch VikingDB status asynchronously."""
        return await self._client._get_vikingdb_status()

    async def _fetch_vlm_status(self) -> Dict[str, Any]:
        """Fetch VLM status asynchronously."""
        return await self._client._get_vlm_status()

    async def _fetch_system_status(self) -> Dict[str, Any]:
        """Fetch system status asynchronously."""
        return await self._client._get_system_status()

    @property
    def queue(self) -> Dict[str, Any]:
        """Get queue system status (sync wrapper)."""
        return run_async(self._fetch_queue_status())

    @property
    def vikingdb(self) -> Dict[str, Any]:
        """Get VikingDB status (sync wrapper)."""
        return run_async(self._fetch_vikingdb_status())

    @property
    def vlm(self) -> Dict[str, Any]:
        """Get VLM status (sync wrapper)."""
        return run_async(self._fetch_vlm_status())

    @property
    def system(self) -> Dict[str, Any]:
        """Get system overall status (sync wrapper)."""
        return run_async(self._fetch_system_status())

    def is_healthy(self) -> bool:
        """Check if system is healthy."""
        status = self.system
        return status.get("is_healthy", False)


class AsyncHTTPClient(BaseClient):
    """Async HTTP Client for OpenViking Server.

    Implements BaseClient interface using HTTP calls.
    Supports auto-loading url/api_key from ovcli.conf when not provided.

    Examples:
        # Explicit url
        client = AsyncHTTPClient(url="http://localhost:1933", api_key="key")

        # Auto-load from ~/.openviking/ovcli.conf
        client = AsyncHTTPClient()
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize AsyncHTTPClient.

        Args:
            url: OpenViking Server URL. If not provided, reads from ovcli.conf.
            api_key: API key for authentication. If not provided, reads from ovcli.conf.
        """
        if url is None:
            config_path = resolve_config_path(None, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
            if config_path:
                cfg = load_json_config(config_path)
                url = cfg.get("url")
                api_key = api_key or cfg.get("api_key")
        if not url:
            raise ValueError(
                "url is required. Pass it explicitly or configure in "
                '~/.openviking/ovcli.conf (key: "url").'
            )
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._user = UserIdentifier.the_default_user()
        self._http: Optional[httpx.AsyncClient] = None
        self._observer: Optional[_HTTPObserver] = None

    # ============= Lifecycle =============

    async def initialize(self) -> None:
        """Initialize the HTTP client."""
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=60.0,
        )
        self._observer = _HTTPObserver(self)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None

    # ============= Internal Helpers =============

    def _handle_response(self, response: httpx.Response) -> Any:
        """Handle HTTP response and extract result or raise exception."""
        try:
            data = response.json()
        except Exception:
            if not response.is_success:
                raise OpenVikingError(
                    f"HTTP {response.status_code}: {response.text or 'empty response'}",
                    code="INTERNAL",
                )
            return None
        if data.get("status") == "error":
            self._raise_exception(data.get("error", {}))
        if not response.is_success:
            raise OpenVikingError(
                data.get("detail", f"HTTP {response.status_code}"),
                code="UNKNOWN",
            )
        return data.get("result")

    def _raise_exception(self, error: Dict[str, Any]) -> None:
        """Raise appropriate exception based on error code."""
        code = error.get("code", "UNKNOWN")
        message = error.get("message", "Unknown error")
        details = error.get("details")

        exc_class = ERROR_CODE_TO_EXCEPTION.get(code, OpenVikingError)

        # Handle different exception constructors
        if exc_class in (InvalidArgumentError,):
            raise exc_class(message, details=details)
        elif exc_class == InvalidURIError:
            uri = details.get("uri", "") if details else ""
            reason = details.get("reason", "") if details else ""
            raise exc_class(uri, reason)
        elif exc_class == NotFoundError:
            resource = details.get("resource", "") if details else ""
            resource_type = details.get("type", "resource") if details else "resource"
            raise exc_class(resource, resource_type)
        elif exc_class == AlreadyExistsError:
            resource = details.get("resource", "") if details else ""
            resource_type = details.get("type", "resource") if details else "resource"
            raise exc_class(resource, resource_type)
        else:
            raise exc_class(message)

    # ============= Resource Management =============

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
        response = await self._http.post(
            "/api/v1/resources",
            json={
                "path": path,
                "target": target,
                "reason": reason,
                "instruction": instruction,
                "wait": wait,
                "timeout": timeout,
            },
        )
        return self._handle_response(response)

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        response = await self._http.post(
            "/api/v1/skills",
            json={
                "data": data,
                "wait": wait,
                "timeout": timeout,
            },
        )
        return self._handle_response(response)

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all processing to complete."""
        response = await self._http.post(
            "/api/v1/system/wait",
            json={"timeout": timeout},
        )
        return self._handle_response(response)

    # ============= File System =============

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
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/fs/ls",
            params={
                "uri": uri,
                "simple": simple,
                "recursive": recursive,
                "output": output,
                "abs_limit": abs_limit,
                "show_all_hidden": show_all_hidden,
                "node_limit": node_limit,
            },
        )
        return self._handle_response(response)

    async def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/fs/tree",
            params={
                "uri": uri,
                "output": output,
                "abs_limit": abs_limit,
                "show_all_hidden": show_all_hidden,
                "node_limit": node_limit,
            },
        )
        return self._handle_response(response)

    async def stat(self, uri: str) -> Dict[str, Any]:
        """Get resource status."""
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/fs/stat",
            params={"uri": uri},
        )
        return self._handle_response(response)

    async def mkdir(self, uri: str) -> None:
        """Create directory."""
        uri = VikingURI.normalize(uri)
        response = await self._http.post(
            "/api/v1/fs/mkdir",
            json={"uri": uri},
        )
        self._handle_response(response)

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource."""
        uri = VikingURI.normalize(uri)
        response = await self._http.request(
            "DELETE",
            "/api/v1/fs",
            params={"uri": uri, "recursive": recursive},
        )
        self._handle_response(response)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource."""
        from_uri = VikingURI.normalize(from_uri)
        to_uri = VikingURI.normalize(to_uri)
        response = await self._http.post(
            "/api/v1/fs/mv",
            json={"from_uri": from_uri, "to_uri": to_uri},
        )
        self._handle_response(response)

    # ============= Content Reading =============

    async def read(self, uri: str) -> str:
        """Read file content."""
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/content/read",
            params={"uri": uri},
        )
        return self._handle_response(response)

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract."""
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/content/abstract",
            params={"uri": uri},
        )
        return self._handle_response(response)

    async def overview(self, uri: str) -> str:
        """Read L1 overview."""
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/content/overview",
            params={"uri": uri},
        )
        return self._handle_response(response)

    # ============= Search =============

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> FindResult:
        """Semantic search without session context."""
        if target_uri:
            target_uri = VikingURI.normalize(target_uri)
        response = await self._http.post(
            "/api/v1/search/find",
            json={
                "query": query,
                "target_uri": target_uri,
                "limit": limit,
                "score_threshold": score_threshold,
                "filter": filter,
            },
        )
        return FindResult.from_dict(self._handle_response(response))

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional[Any] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> FindResult:
        """Semantic search with optional session context."""
        if target_uri:
            target_uri = VikingURI.normalize(target_uri)
        sid = session_id or (session.session_id if session else None)
        response = await self._http.post(
            "/api/v1/search/search",
            json={
                "query": query,
                "target_uri": target_uri,
                "session_id": sid,
                "limit": limit,
                "score_threshold": score_threshold,
                "filter": filter,
            },
        )
        return FindResult.from_dict(self._handle_response(response))

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict[str, Any]:
        """Content search with pattern."""
        uri = VikingURI.normalize(uri)
        response = await self._http.post(
            "/api/v1/search/grep",
            json={
                "uri": uri,
                "pattern": pattern,
                "case_insensitive": case_insensitive,
            },
        )
        return self._handle_response(response)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict[str, Any]:
        """File pattern matching."""
        uri = VikingURI.normalize(uri)
        response = await self._http.post(
            "/api/v1/search/glob",
            json={"pattern": pattern, "uri": uri},
        )
        return self._handle_response(response)

    # ============= Relations =============

    async def relations(self, uri: str) -> List[Any]:
        """Get relations for a resource."""
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/relations",
            params={"uri": uri},
        )
        return self._handle_response(response)

    async def link(self, from_uri: str, to_uris: Union[str, List[str]], reason: str = "") -> None:
        """Create link between resources."""
        from_uri = VikingURI.normalize(from_uri)
        if isinstance(to_uris, str):
            to_uris = VikingURI.normalize(to_uris)
        else:
            to_uris = [VikingURI.normalize(u) for u in to_uris]
        response = await self._http.post(
            "/api/v1/relations/link",
            json={"from_uri": from_uri, "to_uris": to_uris, "reason": reason},
        )
        self._handle_response(response)

    async def unlink(self, from_uri: str, to_uri: str) -> None:
        """Remove link between resources."""
        from_uri = VikingURI.normalize(from_uri)
        to_uri = VikingURI.normalize(to_uri)
        response = await self._http.request(
            "DELETE",
            "/api/v1/relations/link",
            json={"from_uri": from_uri, "to_uri": to_uri},
        )
        self._handle_response(response)

    # ============= Sessions =============

    async def create_session(self) -> Dict[str, Any]:
        """Create a new session."""
        response = await self._http.post(
            "/api/v1/sessions",
            json={},
        )
        return self._handle_response(response)

    async def list_sessions(self) -> List[Any]:
        """List all sessions."""
        response = await self._http.get("/api/v1/sessions")
        return self._handle_response(response)

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session details."""
        response = await self._http.get(f"/api/v1/sessions/{session_id}")
        return self._handle_response(response)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        response = await self._http.delete(f"/api/v1/sessions/{session_id}")
        self._handle_response(response)

    async def commit_session(self, session_id: str) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        response = await self._http.post(f"/api/v1/sessions/{session_id}/commit")
        return self._handle_response(response)

    async def add_message(self, session_id: str, role: str, content: str) -> Dict[str, Any]:
        """Add a message to a session."""
        response = await self._http.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": role, "content": content},
        )
        return self._handle_response(response)

    # ============= Pack =============

    async def export_ovpack(self, uri: str, to: str) -> str:
        """Export context as .ovpack file."""
        uri = VikingURI.normalize(uri)
        response = await self._http.post(
            "/api/v1/pack/export",
            json={"uri": uri, "to": to},
        )
        result = self._handle_response(response)
        return result.get("file", "")

    async def import_ovpack(
        self,
        file_path: str,
        parent: str,
        force: bool = False,
        vectorize: bool = True,
    ) -> str:
        """Import .ovpack file."""
        parent = VikingURI.normalize(parent)
        response = await self._http.post(
            "/api/v1/pack/import",
            json={
                "file_path": file_path,
                "parent": parent,
                "force": force,
                "vectorize": vectorize,
            },
        )
        result = self._handle_response(response)
        return result.get("uri", "")

    # ============= Debug =============

    async def health(self) -> bool:
        """Check server health."""
        try:
            response = await self._http.get("/health")
            data = response.json()
            return data.get("status") == "ok"
        except Exception:
            return False

    # ============= Observer (Internal) =============

    async def _get_queue_status(self) -> Dict[str, Any]:
        """Get queue system status (internal for _HTTPObserver)."""
        response = await self._http.get("/api/v1/observer/queue")
        return self._handle_response(response)

    async def _get_vikingdb_status(self) -> Dict[str, Any]:
        """Get VikingDB status (internal for _HTTPObserver)."""
        response = await self._http.get("/api/v1/observer/vikingdb")
        return self._handle_response(response)

    async def _get_vlm_status(self) -> Dict[str, Any]:
        """Get VLM status (internal for _HTTPObserver)."""
        response = await self._http.get("/api/v1/observer/vlm")
        return self._handle_response(response)

    async def _get_system_status(self) -> Dict[str, Any]:
        """Get system overall status (internal for _HTTPObserver)."""
        response = await self._http.get("/api/v1/observer/system")
        return self._handle_response(response)

    # ============= New methods for BaseClient interface =============

    def session(self, session_id: Optional[str] = None) -> Any:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session if None

        Returns:
            Session object
        """
        from openviking.client.session import Session

        if not session_id:
            result = run_async(self.create_session())
            session_id = result.get("session_id", "")
        return Session(self, session_id, self._user)

    def get_status(self) -> Dict[str, Any]:
        """Get system status.

        Returns:
            Dict containing health status of all components.
        """
        return self._observer.system

    def is_healthy(self) -> bool:
        """Quick health check (synchronous).

        Returns:
            True if all components are healthy, False otherwise.
        """
        return self._observer.is_healthy()

    @property
    def observer(self) -> _HTTPObserver:
        """Get observer service for component status."""
        return self._observer
