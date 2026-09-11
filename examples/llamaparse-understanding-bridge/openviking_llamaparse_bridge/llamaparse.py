# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Minimal async client for the LlamaParse v2 REST API."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from copy import deepcopy
from typing import Any, BinaryIO, Dict, Optional, Set, Tuple, Union
from urllib.parse import quote, urlsplit

import httpx

from .config import Settings


class LlamaParseError(RuntimeError):
    """An error response or invalid response from LlamaParse."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
MAX_ASSET_REDIRECTS = 5


async def _resolve_host_addresses(host: str, port: int) -> Set[IPAddress]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise LlamaParseError(502, "LlamaParse asset host could not be resolved") from exc
    addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    if not addresses:
        raise LlamaParseError(502, "LlamaParse asset host has no address")
    return addresses


async def _validate_asset_url(url: str) -> Tuple[str, Tuple[IPAddress, ...]]:
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise LlamaParseError(502, "LlamaParse asset URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise LlamaParseError(502, "LlamaParse asset URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise LlamaParseError(502, "LlamaParse asset URL must not contain credentials")

    addresses = await _resolve_host_addresses(parsed.hostname, port)
    if any(not address.is_global for address in addresses):
        raise LlamaParseError(502, "LlamaParse asset URL must resolve to a public address")
    ordered_addresses = tuple(
        sorted(addresses, key=lambda address: (address.version, address.packed))
    )
    return parsed.hostname, ordered_addresses


class LlamaParseClient:
    """Translate bridge operations into LlamaParse REST calls."""

    def __init__(
        self,
        settings: Settings,
        *,
        api_client: Optional[httpx.AsyncClient] = None,
        download_client: Optional[httpx.AsyncClient] = None,
    ):
        self._settings = settings
        self._owns_api_client = api_client is None
        self._owns_download_client = download_client is None
        timeout = httpx.Timeout(settings.timeout_seconds)
        self._api = api_client or httpx.AsyncClient(
            base_url=settings.llama_base_url,
            headers={"Authorization": f"Bearer {settings.llama_api_key}"},
            timeout=timeout,
            follow_redirects=True,
        )
        # Do not attach the LlamaCloud key to presigned asset hosts.
        self._downloads = download_client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            # A proxy could resolve the original host again and bypass IP pinning.
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_api_client:
            await self._api.aclose()
        if self._owns_download_client:
            await self._downloads.aclose()

    def _scope_params(self) -> Dict[str, str]:
        params = {}
        if self._settings.organization_id:
            params["organization_id"] = self._settings.organization_id
        if self._settings.project_id:
            params["project_id"] = self._settings.project_id
        return params

    @staticmethod
    async def _request(
        client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            return await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise LlamaParseError(504, "LlamaParse request timed out") from exc
        except httpx.RequestError as exc:
            raise LlamaParseError(502, "LlamaParse request failed") from exc

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error") or payload.get("message")
            if isinstance(detail, str):
                return detail
            if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                return detail["message"]
            if isinstance(detail, list):
                messages = [item.get("msg") for item in detail if isinstance(item, dict)]
                if any(messages):
                    return "; ".join(str(message) for message in messages if message)
        return f"HTTP {response.status_code}"

    @classmethod
    def _json_response(cls, response: httpx.Response) -> Dict[str, Any]:
        if response.is_error:
            raise LlamaParseError(response.status_code, cls._error_message(response))
        try:
            payload = response.json()
        except ValueError as exc:
            raise LlamaParseError(502, "LlamaParse returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise LlamaParseError(502, "LlamaParse returned an invalid response object")
        return payload

    async def upload_file(
        self, filename: str, file: BinaryIO, content_type: Optional[str]
    ) -> Dict[str, Any]:
        """Upload a document and return its LlamaCloud file object."""
        response = await self._request(
            self._api,
            "POST",
            "/api/v1/beta/files",
            params=self._scope_params(),
            data={"purpose": "parse"},
            files={"file": (filename, file, content_type or "application/octet-stream")},
        )
        return self._json_response(response)

    def _parse_configuration(self) -> Dict[str, Any]:
        configuration = deepcopy(self._settings.parse_options)
        configuration["tier"] = self._settings.tier
        configuration["version"] = self._settings.version
        configuration.setdefault("client_name", "openviking-understanding-bridge")

        output_options = configuration.setdefault("output_options", {})
        if not isinstance(output_options, dict):
            raise ValueError("output_options must be an object")
        images_to_save = output_options.setdefault("images_to_save", ["embedded", "layout"])
        if not isinstance(images_to_save, list):
            raise ValueError("output_options.images_to_save must be an array")
        for category in ("embedded", "layout"):
            if category not in images_to_save:
                images_to_save.append(category)

        processing_options = configuration.setdefault("processing_options", {})
        if not isinstance(processing_options, dict):
            raise ValueError("processing_options must be an object")
        if self._settings.cost_optimizer:
            processing_options["cost_optimizer"] = {"enable": True}
        else:
            processing_options.pop("cost_optimizer", None)
        if not processing_options:
            configuration.pop("processing_options")
        return configuration

    async def create_job(
        self, *, file_id: Optional[str] = None, source_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create one parse job for a file ID or a public document URL."""
        if bool(file_id) == bool(source_url):
            raise ValueError("exactly one of file_id or source_url is required")
        payload = self._parse_configuration()
        payload["file_id" if file_id else "source_url"] = file_id or source_url
        response = await self._request(
            self._api,
            "POST",
            "/api/v2/parse",
            params=self._scope_params(),
            json=payload,
        )
        return self._json_response(response)

    async def get_job(self, job_id: str, *, include_result: bool = False) -> Dict[str, Any]:
        """Get job status and optionally request content needed for the ZIP artifact."""
        params = self._scope_params()
        if include_result:
            params["expand"] = "markdown_full,markdown,images_content_metadata"
        encoded_job_id = quote(job_id, safe="")
        response = await self._request(
            self._api, "GET", f"/api/v2/parse/{encoded_job_id}", params=params
        )
        return self._json_response(response)

    async def _request_asset(self, url: str) -> Tuple[httpx.Response, httpx.URL]:
        hostname, addresses = await _validate_asset_url(url)
        original_url = httpx.URL(url)
        last_error: Optional[httpx.RequestError] = None
        for address in addresses:
            # Connect to the validated IP. Keep the original host for HTTP and TLS checks.
            request = self._downloads.build_request(
                "GET",
                original_url.copy_with(host=str(address)),
                headers={
                    "Host": original_url.netloc.decode("ascii"),
                    "Connection": "close",
                },
                extensions={"sni_hostname": hostname},
            )
            try:
                return await self._downloads.send(request), original_url
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
            except httpx.TimeoutException as exc:
                raise LlamaParseError(504, "LlamaParse request timed out") from exc
            except httpx.RequestError as exc:
                raise LlamaParseError(502, "LlamaParse request failed") from exc

        if isinstance(last_error, httpx.TimeoutException):
            raise LlamaParseError(504, "LlamaParse request timed out") from last_error
        raise LlamaParseError(502, "LlamaParse request failed") from last_error

    async def download_asset(self, url: str) -> bytes:
        """Download one presigned result asset without forwarding credentials."""
        current_url = url
        for redirect_count in range(MAX_ASSET_REDIRECTS + 1):
            response, original_url = await self._request_asset(current_url)
            if not response.is_redirect:
                if response.is_error:
                    raise LlamaParseError(response.status_code, self._error_message(response))
                return response.content

            location = response.headers.get("location")
            if not location:
                raise LlamaParseError(502, "LlamaParse asset redirect has no location")
            if redirect_count == MAX_ASSET_REDIRECTS:
                raise LlamaParseError(502, "LlamaParse asset returned too many redirects")
            current_url = str(original_url.join(location))

        raise AssertionError("asset redirect loop exceeded its fixed bound")
