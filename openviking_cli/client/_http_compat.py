# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility bridge for legacy HTTP client entry points."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import httpx

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


def _timeout_configured_outside_call() -> bool:
    if os.getenv("OPENVIKING_TIMEOUT"):
        return True
    config_path = os.getenv("OPENVIKING_CLI_CONFIG_FILE")
    if config_path:
        path = Path(config_path).expanduser()
    else:
        path = Path.home() / ".openviking" / "ovcli.conf"
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and "timeout" in raw


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
        # Heavy local benchmark runs can keep OpenViking search requests queued
        # behind embedding/vector work. Use a larger default read timeout than
        # the upstream SDK's 60s while still respecting explicit caller values
        # and timeouts configured via environment or ovcli.conf.
        if "timeout" not in kwargs:
            try:
                import inspect

                sig = inspect.signature(import_openviking_sdk().AsyncHTTPClient.__init__)
                params = [
                    name
                    for name, param in sig.parameters.items()
                    if name != "self"
                    and param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
                ]
                timeout_index = params.index("timeout")
            except Exception:
                timeout_index = 7
            if len(args) <= timeout_index and not _timeout_configured_outside_call():
                kwargs["timeout"] = 180.0
        super().__init__(*args, **kwargs)

    async def initialize(self) -> None:
        # The upstream SDK uses httpx defaults (max_connections=100). High-parallel
        # tau2 rollouts can exceed that from one shared client and hit PoolTimeout
        # while waiting for a free connection, so raise the pool ceiling.
        headers: Dict[str, str] = {}
        if getattr(self, "_api_key", None):
            headers["X-API-Key"] = self._api_key
        if getattr(self, "_account", None):
            headers["X-OpenViking-Account"] = self._account
        if getattr(self, "_user_id", None):
            headers["X-OpenViking-User"] = self._user_id
        if getattr(self, "_actor_peer_id", None):
            headers["X-OpenViking-Actor-Peer"] = self._actor_peer_id
        headers.update(getattr(self, "_extra_headers", {}) or {})

        max_connections = 512
        max_keepalive = 128
        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=self._timeout,
            params={"profile": "1"} if self._profile_enabled else None,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive,
            ),
        )
        observer_cls = getattr(import_openviking_sdk().client, "_HTTPObserver", None)
        if observer_cls is not None:
            self._observer = observer_cls(self)

    def _raise_exception(self, error: Dict[str, Any]) -> None:
        _raise_legacy_exception(error)


class SyncHTTPClient(import_openviking_sdk().SyncHTTPClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._async_client = AsyncHTTPClient(*args, **kwargs)
