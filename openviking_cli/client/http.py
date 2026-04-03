# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Async HTTP Client for OpenViking.

Implements BaseClient interface using HTTP calls to OpenViking Server.
"""

import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

from openviking.telemetry import TelemetryRequest, normalize_telemetry_request
from openviking.utils.tag_utils import serialize_tags
from openviking_cli.client.base import BaseClient
from openviking_cli.exceptions import (
    AlreadyExistsError,
    DeadlineExceededError,
    EmbeddingFailedError,
    FailedPreconditionError,
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
from openviking_cli.utils.config.ovcli_config import load_ovcli_config
from openviking_cli.utils.uri import VikingURI

# Error code to exception class mapping
ERROR_CODE_TO_EXCEPTION = {
    "INVALID_ARGUMENT": InvalidArgumentError,
    "INVALID_URI": InvalidURIError,
    "NOT_FOUND": NotFoundError,
    "ALREADY_EXISTS": AlreadyExistsError,
    "FAILED_PRECONDITION": FailedPreconditionError,
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

    async def _fetch_models_status(self) -> Dict[str, Any]:
        """Fetch models status asynchronously."""
        return await self._client._get_models_status()

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
    def models(self) -> Dict[str, Any]:
        """Get models status (VLM, Embedding, Rerank) (sync wrapper)."""
        return run_async(self._fetch_models_status())

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
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        account: Optional[str] = None,
        user: Optional[str] = None,
        timeout: float = 60.0,
    ):
        """Initialize AsyncHTTPClient.

        Args:
            url: OpenViking Server URL. If not provided, reads from ovcli.conf.
            api_key: API key for authentication. If not provided, reads from ovcli.conf.
            user_id: User identifier. If not provided, defaults to "default".
            agent_id: Agent identifier. If not provided, reads from ovcli.conf.
            account: Account identifier for multi-tenant auth. Required when using root key
                     to access tenant-scoped APIs. If not provided, reads from ovcli.conf.
            user: User identifier for multi-tenant auth. Required when using root key
                  to access tenant-scoped APIs. If not provided, reads from ovcli.conf.
            timeout: HTTP request timeout in seconds. Default 60.0.
        """
        should_load_cli_config = (
            url is None
            or api_key is None
            or agent_id is None
            or account is None
            or user is None
            or timeout == 60.0
        )
        if should_load_cli_config:
            cli_config = load_ovcli_config()
            if cli_config is not None:
                url = url or cli_config.url
                api_key = api_key or cli_config.api_key
                agent_id = agent_id or cli_config.agent_id
                account = account or cli_config.account
                user = user or cli_config.user
                if timeout == 60.0:  # only override default with config value
                    timeout = cli_config.timeout
        if not url:
            raise ValueError(
                "url is required. Pass it explicitly or configure in "
                '~/.openviking/ovcli.conf (key: "url").'
            )
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._agent_id = agent_id
        self._account = account
        self._user_id = user
        self._user = UserIdentifier.the_default_user()
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._observer: Optional[_HTTPObserver] = None

    # ============= Lifecycle =============

    async def initialize(self) -> None:
        """Initialize the HTTP client."""
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if self._agent_id:
            headers["X-OpenViking-Agent"] = self._agent_id
        if self._account:
            headers["X-OpenViking-Account"] = self._account
        if self._user_id:
            headers["X-OpenViking-User"] = self._user_id
        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=self._timeout,
        )
        self._observer = _HTTPObserver(self)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            try:
                await self._http.aclose()
            except RuntimeError:
                pass
            self._http = None

    # ============= Internal Helpers =============

    def _handle_response_data(self, response: httpx.Response) -> Dict[str, Any]:
        """Handle HTTP response and return the decoded response envelope."""
        try:
            data = response.json()
        except Exception:
            if not response.is_success:
                raise OpenVikingError(
                    f"HTTP {response.status_code}: {response.text or 'empty response'}",
                    code="INTERNAL",
                )
            return {}
        if data.get("status") == "error":
            self._raise_exception(data.get("error", {}))
        if not response.is_success:
            raise OpenVikingError(
                data.get("detail", f"HTTP {response.status_code}"),
                code="UNKNOWN",
            )
        return data

    def _handle_response(self, response: httpx.Response) -> Any:
        """Handle HTTP response and extract result or raise exception."""
        return self._handle_response_data(response).get("result")

    @staticmethod
    def _validate_telemetry(telemetry: TelemetryRequest) -> TelemetryRequest:
        normalize_telemetry_request(telemetry)
        return telemetry

    @staticmethod
    def _attach_telemetry(result: Any, response_data: Dict[str, Any]) -> Any:
        telemetry = response_data.get("telemetry")
        if telemetry is None:
            return result

        if result is None:
            payload: Dict[str, Any] = {}
            payload["telemetry"] = telemetry
            return payload

        if isinstance(result, dict):
            result["telemetry"] = telemetry
            return result

        return result

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

    def _zip_directory(self, dir_path: str) -> str:
        """Create a temporary zip file from a directory."""
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise ValueError(f"Path {dir_path} is not a directory")

        temp_dir = tempfile.gettempdir()
        zip_path = Path(temp_dir) / f"temp_upload_{uuid.uuid4().hex}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in dir_path.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(dir_path)
                    arcname = str(arcname).replace("\\", "/")
                    zipf.write(file_path, arcname=arcname)

        return str(zip_path)

    async def _upload_temp_file(self, file_path: str) -> str:
        """Upload a file to /api/v1/resources/temp_upload and return the temp_file_id."""
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/octet-stream")}
            response = await self._http.post(
                "/api/v1/resources/temp_upload",
                files=files,
            )
        result = self._handle_response(response)
        return result.get("temp_file_id", "")

    # ============= Resource Management =============

    async def add_resource(
        self,
        path: str,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        strict: bool = False,
        ignore_dirs: Optional[str] = None,
        include: Optional[str] = None,
        exclude: Optional[str] = None,
        directly_upload_media: bool = True,
        preserve_structure: Optional[bool] = None,
        tags: Optional[Union[str, List[str]]] = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking."""
        telemetry = self._validate_telemetry(telemetry)
        if to and parent:
            raise ValueError("Cannot specify both 'to' and 'parent' at the same time.")

        request_data = {
            "to": to,
            "parent": parent,
            "reason": reason,
            "instruction": instruction,
            "wait": wait,
            "timeout": timeout,
            "strict": strict,
            "ignore_dirs": ignore_dirs,
            "include": include,
            "exclude": exclude,
            "directly_upload_media": directly_upload_media,
            "tags": serialize_tags(tags),
            "telemetry": telemetry,
        }
        if preserve_structure is not None:
            request_data["preserve_structure"] = preserve_structure

        path_obj = Path(path)
        if path_obj.exists():
            if path_obj.is_dir():
                source_name = path_obj.name
                request_data["source_name"] = source_name
                zip_path = self._zip_directory(path)
                try:
                    temp_file_id = await self._upload_temp_file(zip_path)
                    request_data["temp_file_id"] = temp_file_id
                finally:
                    Path(zip_path).unlink(missing_ok=True)
            elif path_obj.is_file():
                temp_file_id = await self._upload_temp_file(path)
                request_data["temp_file_id"] = temp_file_id
            else:
                request_data["path"] = path
        else:
            request_data["path"] = path

        response = await self._http.post(
            "/api/v1/resources",
            json=request_data,
        )
        response_data = self._handle_response_data(response)
        return self._attach_telemetry(response_data.get("result"), response_data)

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        telemetry = self._validate_telemetry(telemetry)
        request_data = {
            "wait": wait,
            "timeout": timeout,
        }

        if isinstance(data, str):
            path_obj = Path(data)
            if path_obj.exists():
                if path_obj.is_dir():
                    zip_path = self._zip_directory(data)
                    try:
                        temp_file_id = await self._upload_temp_file(zip_path)
                        request_data["temp_file_id"] = temp_file_id
                    finally:
                        Path(zip_path).unlink(missing_ok=True)
                elif path_obj.is_file():
                    temp_file_id = await self._upload_temp_file(data)
                    request_data["temp_file_id"] = temp_file_id
                else:
                    request_data["data"] = data
            else:
                request_data["data"] = data
        else:
            request_data["data"] = data

        response = await self._http.post(
            "/api/v1/skills",
            json={**request_data, "telemetry": telemetry},
        )
        response_data = self._handle_response_data(response)
        return self._attach_telemetry(response_data.get("result"), response_data)

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all processing to complete."""
        http_timeout = timeout if timeout else 600.0
        response = await self._http.post(
            "/api/v1/system/wait",
            json={"timeout": timeout},
            timeout=http_timeout,
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

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        """Read file content.

        Args:
            uri: Viking URI
            offset: Starting line number (0-indexed). Default 0.
            limit: Number of lines to read. -1 means read to end. Default -1.
        """
        uri = VikingURI.normalize(uri)
        response = await self._http.get(
            "/api/v1/content/read",
            params={"uri": uri, "offset": offset, "limit": limit},
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
        telemetry = self._validate_telemetry(telemetry)
        uri = VikingURI.normalize(uri)
        response = await self._http.post(
            "/api/v1/content/write",
            json={
                "uri": uri,
                "content": content,
                "mode": mode,
                "wait": wait,
                "timeout": timeout,
                "telemetry": telemetry,
            },
        )
        response_data = self._handle_response_data(response)
        return self._attach_telemetry(response_data.get("result") or {}, response_data)

    # ============= Search =============

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        tags: Optional[Union[str, List[str]]] = None,
        telemetry: TelemetryRequest = False,
    ) -> FindResult:
        """Semantic search without session context."""
        telemetry = self._validate_telemetry(telemetry)
        if target_uri:
            target_uri = VikingURI.normalize(target_uri)
        actual_limit = node_limit if node_limit is not None else limit
        response = await self._http.post(
            "/api/v1/search/find",
            json={
                "query": query,
                "target_uri": target_uri,
                "limit": actual_limit,
                "score_threshold": score_threshold,
                "filter": filter,
                "tags": serialize_tags(tags),
                "telemetry": telemetry,
            },
        )
        response_data = self._handle_response_data(response)
        return FindResult.from_dict(response_data.get("result") or {})

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional[Any] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        tags: Optional[Union[str, List[str]]] = None,
        telemetry: TelemetryRequest = False,
    ) -> FindResult:
        """Semantic search with optional session context."""
        telemetry = self._validate_telemetry(telemetry)
        if target_uri:
            target_uri = VikingURI.normalize(target_uri)
        actual_limit = node_limit if node_limit is not None else limit
        sid = session_id or (session.session_id if session else None)
        response = await self._http.post(
            "/api/v1/search/search",
            json={
                "query": query,
                "target_uri": target_uri,
                "session_id": sid,
                "limit": actual_limit,
                "score_threshold": score_threshold,
                "filter": filter,
                "tags": serialize_tags(tags),
                "telemetry": telemetry,
            },
        )
        response_data = self._handle_response_data(response)
        return FindResult.from_dict(response_data.get("result") or {})

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Content search with pattern."""
        uri = VikingURI.normalize(uri)
        request_json = {
            "uri": uri,
            "pattern": pattern,
            "case_insensitive": case_insensitive,
        }
        if node_limit is not None:
            request_json["node_limit"] = node_limit
        if exclude_uri is not None:
            request_json["exclude_uri"] = VikingURI.normalize(exclude_uri)
        response = await self._http.post(
            "/api/v1/search/grep",
            json=request_json,
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

    async def create_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new session.

        Args:
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.
        """
        json_body = {"session_id": session_id} if session_id else {}
        response = await self._http.post(
            "/api/v1/sessions",
            json=json_body,
        )
        return self._handle_response(response)

    async def list_sessions(self) -> List[Any]:
        """List all sessions."""
        response = await self._http.get("/api/v1/sessions")
        return self._handle_response(response)

    async def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        """Get session details."""
        params = {}
        if auto_create:
            params["auto_create"] = "true"
        response = await self._http.get(f"/api/v1/sessions/{session_id}", params=params)
        return self._handle_response(response)

    async def get_session_context(
        self, session_id: str, token_budget: int = 128_000
    ) -> Dict[str, Any]:
        """Get assembled session context."""
        response = await self._http.get(
            f"/api/v1/sessions/{session_id}/context",
            params={"token_budget": token_budget},
        )
        return self._handle_response(response)

    async def get_session_archive(self, session_id: str, archive_id: str) -> Dict[str, Any]:
        """Get one completed archive for a session."""
        response = await self._http.get(
            f"/api/v1/sessions/{session_id}/archives/{archive_id}",
        )
        return self._handle_response(response)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        response = await self._http.delete(f"/api/v1/sessions/{session_id}")
        self._handle_response(response)

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Query background task status.

        Args:
            task_id: Task ID (returned by commit)

        Returns:
            Task info dict, or None if not found
        """
        response = await self._http.get(f"/api/v1/tasks/{task_id}")
        if response.status_code == 404:
            return None
        return self._handle_response(response)

    async def commit_session(
        self, session_id: str, telemetry: TelemetryRequest = False
    ) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        telemetry = self._validate_telemetry(telemetry)
        response = await self._http.post(
            f"/api/v1/sessions/{session_id}/commit",
            json={"telemetry": telemetry},
        )
        response_data = self._handle_response_data(response)
        return self._attach_telemetry(response_data.get("result"), response_data)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
    ) -> Dict[str, Any]:
        """Add a message to a session.

        Args:
            session_id: Session ID
            role: Message role ("user" or "assistant")
            content: Text content (simple mode, backward compatible)
            parts: Parts array (full Part support mode)
            created_at: Message creation time (ISO format string)

        If both content and parts are provided, parts takes precedence.
        """
        payload: Dict[str, Any] = {"role": role}
        if parts is not None:
            payload["parts"] = parts
        elif content is not None:
            payload["content"] = content
        else:
            raise ValueError("Either content or parts must be provided")

        if created_at is not None:
            payload["created_at"] = created_at

        response = await self._http.post(
            f"/api/v1/sessions/{session_id}/messages",
            json=payload,
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
        request_data = {
            "parent": parent,
            "force": force,
            "vectorize": vectorize,
        }

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise FileNotFoundError(f"Local ovpack file not found: {file_path}")
        if not file_path_obj.is_file():
            raise ValueError(f"Path {file_path} is not a file")

        temp_file_id = await self._upload_temp_file(file_path)
        request_data["temp_file_id"] = temp_file_id

        response = await self._http.post(
            "/api/v1/pack/import",
            json=request_data,
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

    async def _get_models_status(self) -> Dict[str, Any]:
        """Get models status (VLM, Embedding, Rerank) (internal for _HTTPObserver)."""
        response = await self._http.get("/api/v1/observer/models")
        return self._handle_response(response)

    async def _get_system_status(self) -> Dict[str, Any]:
        """Get system overall status (internal for _HTTPObserver)."""
        response = await self._http.get("/api/v1/observer/system")
        return self._handle_response(response)

    # ============= Admin =============

    async def admin_create_account(self, account_id: str, admin_user_id: str) -> Dict[str, Any]:
        """Create a new account with its first admin user."""
        response = await self._http.post(
            "/api/v1/admin/accounts",
            json={"account_id": account_id, "admin_user_id": admin_user_id},
        )
        return self._handle_response(response)

    async def admin_list_accounts(self) -> List[Any]:
        """List all accounts."""
        response = await self._http.get("/api/v1/admin/accounts")
        return self._handle_response(response)

    async def admin_delete_account(self, account_id: str) -> Dict[str, Any]:
        """Delete an account and all associated users."""
        response = await self._http.delete(f"/api/v1/admin/accounts/{account_id}")
        return self._handle_response(response)

    async def admin_register_user(
        self, account_id: str, user_id: str, role: str = "user"
    ) -> Dict[str, Any]:
        """Register a new user in an account."""
        response = await self._http.post(
            f"/api/v1/admin/accounts/{account_id}/users",
            json={"user_id": user_id, "role": role},
        )
        return self._handle_response(response)

    async def admin_list_users(self, account_id: str) -> List[Any]:
        """List all users in an account."""
        response = await self._http.get(f"/api/v1/admin/accounts/{account_id}/users")
        return self._handle_response(response)

    async def admin_remove_user(self, account_id: str, user_id: str) -> Dict[str, Any]:
        """Remove a user from an account."""
        response = await self._http.delete(f"/api/v1/admin/accounts/{account_id}/users/{user_id}")
        return self._handle_response(response)

    async def admin_set_role(self, account_id: str, user_id: str, role: str) -> Dict[str, Any]:
        """Change a user's role."""
        response = await self._http.put(
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/role",
            json={"role": role},
        )
        return self._handle_response(response)

    async def admin_regenerate_key(self, account_id: str, user_id: str) -> Dict[str, Any]:
        """Regenerate a user's API key. Old key is immediately invalidated."""
        response = await self._http.post(
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/key",
        )
        return self._handle_response(response)

    # ============= New methods for BaseClient interface =============

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> Any:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session if None
            must_exist: If True and session_id is provided, raises NotFoundError
                        when the session does not exist.
                        If session_id is None, must_exist is ignored.

        Returns:
            Session object

        Raises:
            NotFoundError: If must_exist=True and the session does not exist.
        """
        from openviking.client.session import Session

        if not session_id:
            result = run_async(self.create_session())
            session_id = result.get("session_id", "")
        elif must_exist:
            # get_session() raises NotFoundError (via _handle_response) for 404.
            run_async(self.get_session(session_id))
        return Session(self, session_id, self._user)

    async def session_exists(self, session_id: str) -> bool:
        """Check whether a session exists in storage.

        Args:
            session_id: Session ID to check

        Returns:
            True if the session exists, False otherwise
        """
        try:
            await self.get_session(session_id)
            return True
        except NotFoundError:
            return False

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
