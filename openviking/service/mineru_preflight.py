# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Startup preflight for the MinerU API.

Polls the MinerU ``/health`` endpoint so that endpoint misconfiguration, a
stopped service, or a protocol incompatibility is surfaced at service
initialization instead of on the first PDF import.
"""

import asyncio
import time

import httpx

HEALTH_TIMEOUT = 5.0          # Total polling timeout in seconds.
HEALTH_POLL_INTERVAL = 0.2    # Delay between polls in seconds.
HEALTH_REQUEST_TIMEOUT = 0.5  # Per-request timeout in seconds.


async def wait_for_mineru_ready(endpoint: str, timeout: float = HEALTH_TIMEOUT) -> None:
    """
    Poll the MinerU ``/health`` endpoint until it reports ready.

    A healthy response is HTTP 200 with a JSON object carrying a non-empty
    ``protocol_version`` and ``status == "healthy"``.

    Args:
        endpoint: MinerU base URL (e.g. ``http://127.0.0.1:8000``).
        timeout: Total time to poll before giving up, in seconds.

    Raises:
        RuntimeError: If the endpoint does not report ready within ``timeout``
            seconds. The message includes the health URL and the last error.
    """
    health_url = f"{endpoint.rstrip('/')}/health"
    deadline = time.monotonic() + timeout
    last_error = "no response"

    async with httpx.AsyncClient(timeout=HEALTH_REQUEST_TIMEOUT) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(health_url)
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc) or exc.__class__.__name__
            else:
                if isinstance(payload, dict):
                    protocol = payload.get("protocol_version")
                    status = payload.get("status")
                else:
                    protocol = status = None

                if response.status_code == 200 and protocol and status == "healthy":
                    return

                last_error = (
                    f"unexpected response: HTTP {response.status_code}, "
                    f"protocol_version={protocol!r}, status={status!r}"
                )

            await asyncio.sleep(HEALTH_POLL_INTERVAL)

    raise RuntimeError(
        f"MinerU startup preflight failed for {health_url} after {timeout}s. "
        f"Last error: {last_error}. "
        "Start a compatible mineru-api service or correct pdf.mineru_endpoint."
    )
