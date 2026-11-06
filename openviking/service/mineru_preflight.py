# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Startup preflight for the MinerU API.

Polls the MinerU ``/health`` endpoint so that endpoint misconfiguration, a
stopped service, or a protocol incompatibility is surfaced at service
initialization instead of on the first PDF import.

The check is protocol-aware. A self-hosted deployment answers ``/health`` with the
MinerU contract this module validates. The hosted service does not expose that route
at all -- probing it returns 404 -- so a token-configured endpoint is checked for
reachability and authentication instead, and a failure there is reported as such
rather than as a protocol mismatch.
"""

import asyncio
import time
from typing import Optional

import httpx

HEALTH_TIMEOUT = 5.0  # Total polling timeout in seconds.
HEALTH_POLL_INTERVAL = 0.2  # Delay between polls in seconds.
HEALTH_REQUEST_TIMEOUT = 0.5  # Per-request timeout in seconds.


async def wait_for_mineru_ready(
    endpoint: str,
    timeout: float = HEALTH_TIMEOUT,
    hosted: bool = False,
    token: Optional[str] = None,
) -> None:
    """
    Poll the MinerU ``/health`` endpoint until it reports ready.

    A healthy response is HTTP 200 with a JSON object carrying a non-empty
    ``protocol_version`` and ``status == "healthy"``.

    Args:
        endpoint: MinerU base URL (e.g. ``http://127.0.0.1:8000``).
        hosted: When true, ``endpoint`` is the hosted service, which serves no
            ``/health`` route. The probe then only checks that the API is reachable
            and that the token is accepted, instead of demanding the self-hosted
            health contract the hosted API cannot satisfy.
        token: Bearer token for the hosted API, sent on the probe so an
            authentication problem surfaces here rather than on the first import.
        timeout: Total time to poll before giving up, in seconds.

    Raises:
        RuntimeError: If the endpoint does not report ready within ``timeout``
            seconds. The message includes the health URL and the last error.
    """
    health_url = f"{endpoint.rstrip('/')}/health"
    # The hosted API exposes no /health; probing one returns 404, which says nothing
    # about whether the service is up. Probe a real route and judge reachability.
    probe_url = (
        f"{endpoint.rstrip('/')}/extract-results/batch/__preflight__" if hosted else health_url
    )
    probe_headers = {"Authorization": f"Bearer {token}"} if (hosted and token) else {}
    deadline = time.monotonic() + timeout
    last_error = "no response"

    async with httpx.AsyncClient(timeout=HEALTH_REQUEST_TIMEOUT) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(probe_url, headers=probe_headers)
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc) or exc.__class__.__name__
            else:
                if hosted:
                    # Reachable and not rejected on credentials is all the hosted
                    # service can tell us without doing real work.
                    if response.status_code not in (401, 403):
                        return
                    last_error = f"HTTP {response.status_code} from {probe_url}"
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
        f"MinerU startup preflight failed for {probe_url} after {timeout}s. "
        f"Last error: {last_error}. "
        "Start a compatible mineru-api service or correct pdf.mineru_endpoint."
    )
