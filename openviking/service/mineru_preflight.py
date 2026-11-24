# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Health discovery shared by MinerU startup and PDF parsing."""

import asyncio
import time
from typing import Any

import httpx

HEALTH_TIMEOUT = 5.0
HEALTH_POLL_INTERVAL = 0.2
HEALTH_REQUEST_TIMEOUT = 0.5


def mineru_base_url(endpoint: str) -> str:
    """Normalize a base URL or an explicitly configured parsing URL."""
    return endpoint.rstrip("/").removesuffix("/file_parse")


async def wait_for_mineru_ready(
    endpoint: str,
    timeout: float = HEALTH_TIMEOUT,
    *,
    api_mode: str = "auto",
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return the healthy API mode and its health response."""
    base_url = mineru_base_url(endpoint)
    urls = {"v1": f"{base_url.removesuffix('/v1')}/v1/health", "legacy": f"{base_url}/health"}
    modes = ("v1", "legacy") if api_mode == "auto" else (api_mode,)
    deadline = time.monotonic() + timeout
    errors = dict.fromkeys(modes, "no response")

    async with httpx.AsyncClient(timeout=HEALTH_REQUEST_TIMEOUT, headers=headers) as client:
        while time.monotonic() < deadline:
            for mode in modes:
                try:
                    remaining = max(0, deadline - time.monotonic())
                    response = await asyncio.wait_for(client.get(urls[mode]), remaining)
                    payload = response.json()
                except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
                    errors[mode] = str(exc) or exc.__class__.__name__
                    continue

                if not isinstance(payload, dict):
                    payload = {}
                status = payload.get("status")
                if mode == "v1":
                    capability = bool(payload.get("version")) or isinstance(
                        payload.get("features"), dict
                    )
                    ready = capability and status in ("ok", "healthy")
                else:
                    ready = bool(payload.get("protocol_version")) and status == "healthy"
                if response.status_code == 200 and ready:
                    return mode, payload

                errors[mode] = f"HTTP {response.status_code}, status={status!r}"

            await asyncio.sleep(min(HEALTH_POLL_INTERVAL, max(0, deadline - time.monotonic())))

    attempts = ", ".join(f"{mode} {urls[mode]} ({errors[mode]})" for mode in modes)
    raise RuntimeError(
        f"MinerU startup preflight failed after {timeout}s. Probes: {attempts}. "
        "Start a compatible MinerU service or correct pdf.mineru_endpoint."
    )
