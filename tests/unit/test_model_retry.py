# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared model retry helpers."""

import pytest

from openviking.utils.model_retry import classify_api_error, retry_async, retry_sync


def test_classify_api_error_recognizes_request_burst_too_fast():
    assert classify_api_error(RuntimeError("RequestBurstTooFast")) == "transient"


def test_retry_sync_retries_transient_error_until_success():
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("429 TooManyRequests")
        return "ok"

    assert retry_sync(_call, max_retries=3) == "ok"
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_retry_async_does_not_retry_unknown_error():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        raise RuntimeError("some unexpected validation failure")

    with pytest.raises(RuntimeError):
        await retry_async(_call, max_retries=3)

    assert attempts["count"] == 1
