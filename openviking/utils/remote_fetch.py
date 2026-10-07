# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Guarded HTTP downloads for remote repository artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from openviking.utils.network_guard import (
    RequestValidator,
    build_httpx_request_validation_hooks,
    ensure_public_remote_target,
)


async def download_remote_file(
    url: str,
    destination: str | Path,
    *,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 1800.0,
    request_validator: RequestValidator = ensure_public_remote_target,
) -> None:
    """Download a remote file while validating every request URL."""
    import httpx

    client_kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
    }
    event_hooks = build_httpx_request_validation_hooks(request_validator)
    if event_hooks:
        client_kwargs["event_hooks"] = event_hooks
        client_kwargs["trust_env"] = False

    path = Path(destination)
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                with path.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
