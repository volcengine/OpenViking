# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Startup preflight for self-hosted and hosted MinerU APIs.
"""

import asyncio
import time

import httpx

HEALTH_TIMEOUT = 5.0  # Total polling timeout in seconds.
HEALTH_POLL_INTERVAL = 0.2  # Delay between polls in seconds.
HEALTH_REQUEST_TIMEOUT = 0.5  # Per-request timeout in seconds.


async def wait_for_mineru_ready(
    endpoint: str, timeout: float = HEALTH_TIMEOUT, *, token: str | None = None
) -> None:
    """Check the self-hosted health endpoint or probe the v4 batch lookup."""
    base = endpoint.rstrip("/")
    if token:
        # An unused batch ID exercises the API without submitting a document.
        url = f"{base}/extract-results/batch/00000000-0000-0000-0000-000000000000"
        headers = {"Authorization": f"Bearer {token}"}
    else:
        url = f"{base}/health"
        headers = {}
    deadline = time.monotonic() + timeout
    last_error = "no response"

    async with httpx.AsyncClient(timeout=HEALTH_REQUEST_TIMEOUT) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url, headers=headers)
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc) or exc.__class__.__name__
            else:
                payload = payload if isinstance(payload, dict) else {}
                if token:
                    code = payload.get("code")
                    if response.status_code == 404 or (
                        response.status_code == 200 and code in (0, -60012)
                    ):
                        return
                    last_error = f"unexpected response: HTTP {response.status_code}, code={code!r}"
                else:
                    protocol = payload.get("protocol_version")
                    status = payload.get("status")
                    if response.status_code == 200 and protocol and status == "healthy":
                        return
                    last_error = (
                        f"unexpected response: HTTP {response.status_code}, "
                        f"protocol_version={protocol!r}, status={status!r}"
                    )

            await asyncio.sleep(HEALTH_POLL_INTERVAL)

    raise RuntimeError(
        f"MinerU startup preflight failed for {url} after {timeout}s. "
        f"Last error: {last_error}. "
        "Start a compatible mineru-api service or correct pdf.mineru_endpoint."
    )
