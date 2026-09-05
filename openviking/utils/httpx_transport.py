# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTPX transport helpers for connecting to pre-validated addresses."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Sequence
from contextlib import suppress
from typing import Optional

import httpcore
import httpx

_HTTPCORE_EXCEPTION_MAP: tuple[tuple[type[Exception], type[httpx.HTTPError]], ...] = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout),
    (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.TimeoutException, httpx.TimeoutException),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError),
    (httpcore.NetworkError, httpx.NetworkError),
    (httpcore.ProxyError, httpx.ProxyError),
    (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.ProtocolError, httpx.ProtocolError),
)


def _map_httpcore_exception(exc: Exception) -> Optional[httpx.HTTPError]:
    for source_type, target_type in _HTTPCORE_EXCEPTION_MAP:
        if isinstance(exc, source_type):
            return target_type(str(exc))
    return None


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for part in self._stream:
                yield part
        except Exception as exc:
            mapped_exc = _map_httpcore_exception(exc)
            if mapped_exc is None:
                raise
            raise mapped_exc from exc

    async def aclose(self) -> None:
        if hasattr(self._stream, "aclose"):
            await self._stream.aclose()


class RacingAddressNetworkBackend(httpcore.AsyncNetworkBackend):
    """Race TCP connections to approved addresses and return one winning stream."""

    def __init__(
        self,
        addresses: Sequence[str],
        *,
        network_backend: Optional[httpcore.AsyncNetworkBackend] = None,
        stagger_delay: float = 0.25,
    ) -> None:
        if not addresses:
            raise ValueError("At least one validated address is required.")
        normalized_addresses: list[str] = []
        for address in addresses:
            try:
                normalized_address = str(ipaddress.ip_address(address))
            except ValueError as exc:
                raise ValueError(f"Validated address '{address}' is not an IP literal.") from exc
            if normalized_address not in normalized_addresses:
                normalized_addresses.append(normalized_address)
        self._addresses = tuple(normalized_addresses)
        self._network_backend = network_backend or httpcore.AnyIOBackend()
        self._stagger_delay = max(0.0, stagger_delay)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[httpcore.SOCKET_OPTION]] = None,
    ) -> httpcore.AsyncNetworkStream:
        socket_options = tuple(socket_options) if socket_options is not None else None
        if len(self._addresses) == 1:
            return await self._network_backend.connect_tcp(
                self._addresses[0],
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout is not None else None
        stagger_delay = self._stagger_delay
        if timeout is not None:
            # Fit every approved address inside the caller's one connect
            # deadline while leaving time for the final attempt to complete.
            stagger_delay = min(
                stagger_delay,
                max(0.0, timeout) / (len(self._addresses) + 1),
            )

        async def _connect(address: str) -> httpcore.AsyncNetworkStream:
            return await self._network_backend.connect_tcp(
                address,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        tasks: list[asyncio.Task[httpcore.AsyncNetworkStream]] = []
        pending: set[asyncio.Task[httpcore.AsyncNetworkStream]] = set()
        errors: list[Exception] = []
        winner: Optional[asyncio.Task[httpcore.AsyncNetworkStream]] = None
        next_address_index = 0
        next_launch_at = loop.time()
        timed_out = False
        try:
            while pending or next_address_index < len(self._addresses):
                now = loop.time()
                if deadline is not None and now >= deadline:
                    timed_out = True
                    break

                if next_address_index < len(self._addresses) and (
                    not pending or now >= next_launch_at
                ):
                    task = asyncio.create_task(_connect(self._addresses[next_address_index]))
                    tasks.append(task)
                    pending.add(task)
                    next_address_index += 1
                    next_launch_at = now + stagger_delay
                    continue

                remaining_timeout = None
                if deadline is not None:
                    remaining_timeout = deadline - now
                if next_address_index < len(self._addresses):
                    launch_timeout = max(0.0, next_launch_at - now)
                    remaining_timeout = (
                        launch_timeout
                        if remaining_timeout is None
                        else min(remaining_timeout, launch_timeout)
                    )
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    continue
                for task in done:
                    try:
                        stream = task.result()
                    except Exception as exc:
                        errors.append(exc)
                    else:
                        winner = task
                        await self._cancel_and_close_losers(tasks, winner)
                        return stream

            if timed_out:
                raise httpcore.ConnectTimeout(
                    "Timed out while connecting to the validated addresses."
                ) from (errors[-1] if errors else None)
            if errors:
                raise errors[-1]
            raise httpcore.ConnectError("All validated connection attempts failed.")
        finally:
            if winner is None:
                await self._cancel_and_close_losers(tasks, None)

    @staticmethod
    async def _cancel_and_close_losers(
        tasks: Sequence[asyncio.Task[httpcore.AsyncNetworkStream]],
        winner: Optional[asyncio.Task[httpcore.AsyncNetworkStream]],
    ) -> None:
        losers = [task for task in tasks if task is not winner]
        for task in losers:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*losers, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                continue
            with suppress(Exception):
                await result.aclose()

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[httpcore.SOCKET_OPTION]] = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._network_backend.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


class PinnedAddressHTTPTransport(httpx.AsyncBaseTransport):
    """Send one HTTP request over the first approved TCP connection to succeed."""

    def __init__(
        self,
        addresses: Sequence[str],
        *,
        network_backend: Optional[httpcore.AsyncNetworkBackend] = None,
        stagger_delay: float = 0.25,
    ) -> None:
        backend = RacingAddressNetworkBackend(
            addresses,
            network_backend=network_backend,
            stagger_delay=stagger_delay,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=backend,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            core_response = await self._pool.handle_async_request(core_request)
        except Exception as exc:
            mapped_exc = _map_httpcore_exception(exc)
            if mapped_exc is None:
                raise
            raise mapped_exc from exc

        if not isinstance(core_response.stream, AsyncIterable):
            raise TypeError("HTTP core returned a non-async response stream.")
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_ResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
