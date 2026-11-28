# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared wire format and transport for internal store services."""

import asyncio
import json
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Generic, TypeVar

import httpx
from async_timeout import timeout
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.field_encryption import FieldCipher
from openviking_cli.utils.config.http_store_config import HTTPStoreConfig

T = TypeVar("T")


class WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class ResultEnvelope(WireModel, Generic[T]):
    result: T


class ServiceError(WireModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(WireModel):
    error: ServiceError


@dataclass(frozen=True)
class Route:
    path: str
    request: type[WireModel]
    response: Any
    mutating: bool = False
    request_secrets: tuple[str, ...] = ()
    response_secrets: tuple[str, ...] = ()

    @cached_property
    def response_adapter(self) -> TypeAdapter:
        return TypeAdapter(self.response)


class InternalServiceError(RuntimeError):
    def __init__(self, code: str, status_code: int | None, outcome_unknown: bool):
        # Remote diagnostics can contain private data.
        super().__init__(f"Internal store request failed (HTTP {status_code or 'unavailable'})")
        self.code = code
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown


class InternalStoreHTTP:
    def __init__(
        self,
        endpoint: HTTPStoreConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._endpoint = endpoint
        self._headers = endpoint.headers()
        self._field_cipher = (
            FieldCipher(endpoint.decode_field_encryption_key(endpoint.field_encryption_key))
            if endpoint.field_encryption_key is not None
            else None
        )
        self._transport = transport
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: httpx.AsyncClient | None = None
        self.is_closed = False

    async def load(self) -> None:
        """Bind connection reuse to the lifecycle loop; no remote startup call."""
        if self.is_closed:
            raise RuntimeError("Internal store client is closed")
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

    async def close(self) -> None:
        self.is_closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._headers,
            timeout=self._endpoint.http_timeout_seconds,
            limits=httpx.Limits(keepalive_expiry=30),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )

    async def request(self, route: Route, request: WireModel) -> Any:
        if self.is_closed:
            raise RuntimeError("Internal store client is closed")
        if type(request) is not route.request:
            raise TypeError(f"Expected {route.request.__name__} for {route.path}")

        payload = request.model_dump(mode="json")
        if self._field_cipher is not None:
            try:
                payload = self._field_cipher.transform(payload, route.request_secrets, encrypt=True)
            except (ValueError, TypeError):
                raise InternalServiceError("invalid_request", None, False) from None

        async def invoke() -> Any:
            try:
                async with timeout(self._endpoint.http_timeout_seconds):
                    url = self._endpoint.base_url + route.path
                    if asyncio.get_running_loop() is self._loop:
                        if self._client is None:
                            self._client = self._new_client()
                        response = await self._client.post(url, json=payload)
                    else:
                        # Other worker loops can call the store without sharing a loop-bound pool.
                        async with self._new_client() as client:
                            response = await client.post(url, json=payload)
            except (httpx.HTTPError, asyncio.TimeoutError):
                raise InternalServiceError("transport_error", None, route.mutating) from None
            status = response.status_code
            try:
                body = response.json()
                if status == 200:
                    if not isinstance(body, dict) or set(body) != {"result"}:
                        raise ValueError("Expected a result envelope")
                    result = body["result"]
                    if self._field_cipher is not None:
                        result = self._field_cipher.transform(
                            result, route.response_secrets, encrypt=False
                        )
                    return route.response_adapter.validate_json(json.dumps(result), strict=True)
                error = ErrorResponse.model_validate(body).error
            except (ValidationError, ValueError, TypeError):
                raise InternalServiceError("invalid_response", status, route.mutating) from None
            self.raise_service_error(error, status, route.mutating)

        return await run_to_completion(invoke) if route.mutating else await invoke()

    @staticmethod
    def raise_service_error(error: ServiceError, status: int, mutating: bool) -> None:
        raise InternalServiceError(error.code, status, mutating and (status >= 500 or status < 400))
