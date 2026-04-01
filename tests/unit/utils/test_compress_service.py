# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for CompressService."""

import pytest

from openviking.utils.compress_service import CompressService


def test_compress_service_init_defaults():
    service = CompressService()
    assert service.max_abstract_length == 128


def test_compress_service_init_custom():
    service = CompressService(max_abstract_length=256)
    assert service.max_abstract_length == 256


@pytest.mark.asyncio
async def test_compress_directory_no_viking_fs(monkeypatch):
    """Returns error when VikingFS is not available."""
    monkeypatch.setattr("openviking.utils.compress_service.get_viking_fs", lambda: None)
    service = CompressService()
    result = await service.compress_directory("viking://user/memories/", ctx=None)
    assert result["status"] == "error"
    assert "VikingFS" in result["message"]
