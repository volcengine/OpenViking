# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility bridge for legacy HTTP client entry points."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from openviking_cli._sdk_import import import_openviking_sdk
from openviking_cli.exceptions import (
    AbortedError,
    AlreadyExistsError,
    ConflictError,
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
    ResourceExhaustedError,
    SessionExpiredError,
    UnauthenticatedError,
    UnavailableError,
    UnimplementedError,
    VLMFailedError,
)

ERROR_CODE_TO_EXCEPTION = {
    "INVALID_ARGUMENT": InvalidArgumentError,
    "INVALID_URI": InvalidURIError,
    "NOT_FOUND": NotFoundError,
    "ALREADY_EXISTS": AlreadyExistsError,
    "CONFLICT": ConflictError,
    "FAILED_PRECONDITION": FailedPreconditionError,
    "ABORTED": AbortedError,
    "UNAUTHENTICATED": UnauthenticatedError,
    "PERMISSION_DENIED": PermissionDeniedError,
    "RESOURCE_EXHAUSTED": ResourceExhaustedError,
    "UNAVAILABLE": UnavailableError,
    "INTERNAL": InternalError,
    "DEADLINE_EXCEEDED": DeadlineExceededError,
    "UNIMPLEMENTED": UnimplementedError,
    "NOT_INITIALIZED": NotInitializedError,
    "PROCESSING_ERROR": ProcessingError,
    "EMBEDDING_FAILED": EmbeddingFailedError,
    "VLM_FAILED": VLMFailedError,
    "SESSION_EXPIRED": SessionExpiredError,
    "UNKNOWN": OpenVikingError,
}


class _CompatHTTPObserver:
    def __init__(self, client: "AsyncHTTPClient"):
        self._client = client

    @property
    def queue(self) -> Dict[str, Any]:
        from openviking_cli.utils import run_async

        return run_async(self._client._get_queue_status())

    @property
    def vikingdb(self) -> Dict[str, Any]:
        from openviking_cli.utils import run_async

        return run_async(self._client._get_vikingdb_status())

    @property
    def models(self) -> Dict[str, Any]:
        from openviking_cli.utils import run_async

        return run_async(self._client._get_models_status())

    @property
    def system(self) -> Dict[str, Any]:
        from openviking_cli.utils import run_async

        return run_async(self._client._get_system_status())

    def is_healthy(self) -> bool:
        return self.system.get("is_healthy", False)


def _raise_legacy_exception(error: Dict[str, Any]) -> None:
    code = error.get("code", "UNKNOWN")
    message = error.get("message", "Unknown error")
    details = error.get("details")
    exc_class = ERROR_CODE_TO_EXCEPTION.get(code, OpenVikingError)

    if exc_class == OpenVikingError:
        raise exc_class(message, code=code, details=details)
    if exc_class in (
        InvalidArgumentError,
        FailedPreconditionError,
        ResourceExhaustedError,
        AbortedError,
        UnimplementedError,
    ):
        raise exc_class(message, details=details)
    if exc_class == InvalidURIError:
        uri = details.get("uri", "") if details else ""
        reason = details.get("reason", "") if details else ""
        raise exc_class(uri, reason)
    if exc_class == NotFoundError:
        resource = details.get("resource", "") if details else ""
        resource_type = details.get("type", "resource") if details else "resource"
        raise exc_class(resource, resource_type)
    if exc_class == AlreadyExistsError:
        resource = details.get("resource", "") if details else ""
        resource_type = details.get("type", "resource") if details else "resource"
        raise exc_class(resource, resource_type)
    raise exc_class(message)


class AsyncHTTPClient(import_openviking_sdk().AsyncHTTPClient):
    def __init__(self, *args, **kwargs):
        sdk = import_openviking_sdk()
        legacy_agent_id = kwargs.get("agent_id")
        if legacy_agent_id is None and kwargs.get("actor_peer_id") is None:
            try:
                cli_config = sdk.config.load_ovcli_config()
            except ValueError:
                cli_config = None
            if cli_config is not None:
                legacy_agent_id = getattr(cli_config, "agent_id", None)
        super().__init__(*args, **kwargs)
        self._legacy_agent_id = legacy_agent_id

    def _raise_exception(self, error: Dict[str, Any]) -> None:
        _raise_legacy_exception(error)

    async def initialize(self) -> None:
        headers: Dict[str, str] = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if self._account:
            headers["X-OpenViking-Account"] = self._account
        if self._user_id:
            headers["X-OpenViking-User"] = self._user_id
        if self._actor_peer_id:
            headers["X-OpenViking-Actor-Peer"] = self._actor_peer_id
        headers.update(self._extra_headers)

        from openviking_cli.client import http as legacy_http_module

        self._http = legacy_http_module.httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=self._timeout,
            params={"profile": "1"} if self._profile_enabled else None,
        )
        self._observer = _CompatHTTPObserver(self)

    @staticmethod
    def _normalize_context_type(value: Optional[Any]) -> Optional[Any]:
        if isinstance(value, list):
            return [getattr(item, "value", item) for item in value]
        return getattr(value, "value", value)

    def _attach_legacy_agent_id(self, payload: Dict[str, Any]) -> None:
        if self._legacy_agent_id:
            payload["agent_id"] = self._legacy_agent_id

    async def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        actual_limit = node_limit if node_limit is not None else limit
        payload = {
            "query": query,
            "target_uri": self._normalize_target_uri(target_uri),
            "limit": actual_limit,
            "score_threshold": score_threshold,
            "filter": filter,
            "context_type": self._normalize_context_type(context_type),
            "telemetry": telemetry,
        }
        if tags is not None:
            payload["tags"] = tags
        self._attach_legacy_agent_id(payload)
        response = await self._http.post("/api/v1/search/find", json=payload)
        return self._handle_response_data(response).get("result", {})

    async def search(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        session: Optional[Any] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        actual_limit = node_limit if node_limit is not None else limit
        sid = session_id or (session.session_id if session else None)
        payload = {
            "query": query,
            "target_uri": self._normalize_target_uri(target_uri),
            "session_id": sid,
            "limit": actual_limit,
            "score_threshold": score_threshold,
            "filter": filter,
            "context_type": self._normalize_context_type(context_type),
            "telemetry": telemetry,
        }
        if tags is not None:
            payload["tags"] = tags
        self._attach_legacy_agent_id(payload)
        response = await self._http.post("/api/v1/search/search", json=payload)
        return self._handle_response_data(response).get("result", {})

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
        peer_id: str | None = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        if self._legacy_agent_id and peer_id is not None:
            raise InvalidArgumentError("peer_id cannot be used with legacy agent_id")
        payload: Dict[str, Any] = {"role": role}
        if parts is not None:
            payload["parts"] = parts
        elif content is not None:
            payload["content"] = content
        else:
            raise ValueError("Either content or parts must be provided")
        if created_at is not None:
            payload["created_at"] = created_at
        if peer_id is not None:
            payload["peer_id"] = peer_id
        if self._legacy_agent_id and role == "assistant":
            payload["agent_id"] = self._legacy_agent_id
        if telemetry is not False:
            payload["telemetry"] = telemetry
        session_path = self._path_segment(session_id)
        response = await self._http.post(f"/api/v1/sessions/{session_path}/messages", json=payload)
        return self._handle_response_data(response).get("result", {})


class SyncHTTPClient(import_openviking_sdk().SyncHTTPClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._async_client = AsyncHTTPClient(*args, **kwargs)
