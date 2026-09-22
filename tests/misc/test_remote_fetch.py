# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for guarded remote file downloads."""

from unittest.mock import patch

import httpx
import pytest

from openviking.utils.remote_fetch import download_remote_file
from openviking_cli.exceptions import PermissionDeniedError


@pytest.mark.asyncio
async def test_download_rejects_redirect_to_non_public_target(tmp_path) -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={
                "Location": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs) -> httpx.AsyncClient:
        return real_async_client(transport=transport, **kwargs)

    destination = tmp_path / "repo.zip"
    with patch("httpx.AsyncClient", client_factory):
        with pytest.raises(PermissionDeniedError, match="non-public address"):
            await download_remote_file("https://93.184.216.34/repo.zip", destination)

    assert requested_urls == ["https://93.184.216.34/repo.zip"]
    assert not destination.exists()
