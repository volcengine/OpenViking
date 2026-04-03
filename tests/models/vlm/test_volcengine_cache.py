# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VolcEngineVLM cache logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM as VLMClass


def make_message(role: str, content: str, cache_control: bool = False) -> dict:
    """Helper to create a message dict."""
    msg = {"role": role, "content": content}
    if cache_control:
        msg["cache_control"] = {"type": "ephemeral"}
    return msg


class TestGetOrCreateFromSegments:
    """Tests for _get_or_create_from_segments method."""

    def _create_vlm_with_mock_cache(self):
        """Create a VLM instance with mocked dependencies."""
        vlm = VLMClass(
            model="test-model",
            api_key="test-key",
            api_base="https://ark.cn-beijing.volces.com/api/v3",
        )
        # Mock the cache
        vlm._response_cache = MagicMock()
        vlm.get_async_client = MagicMock()
        return vlm

    def test_single_segment_with_cache_hit(self):
        """Test: Single segment, cache exists."""
        vlm = self._create_vlm_with_mock_cache()

        # 只有一个 segment [msg0, msg1(cache_control)]
        segments = [
            [make_message("system", "You are a helpful assistant"), make_message("user", "Hello", cache_control=True)]
        ]

        # Mock cache hit
        vlm._response_cache.get.return_value = "resp_123"

        result = VLMClass._get_or_create_from_segments(vlm, segments, 1)

        assert result == "resp_123"
        vlm._response_cache.get.assert_called_once()
        vlm.get_async_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_segment_cache_miss_create_new(self):
        """Test: Single segment, cache miss, create new cache."""
        vlm = self._create_vlm_with_mock_cache()

        segments = [
            [make_message("system", "You are a helpful assistant"), make_message("user", "Hello", cache_control=True)]
        ]

        # Mock cache miss
        vlm._response_cache.get.return_value = None

        # Mock API response
        mock_response = MagicMock()
        mock_response.id = "resp_new_123"
        mock_client = AsyncMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        vlm.get_async_client.return_value = mock_client

        result = await vlm._get_or_create_from_segments(segments, 1)

        assert result == "resp_new_123"
        vlm._response_cache.get.assert_called_once()
        vlm._response_cache.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_two_segments_both_cached(self):
        """Test: Two segments, both have cache."""
        vlm = self._create_vlm_with_mock_cache()

        # segments = [[msg0, msg1(cc)], [msg2, msg3(cc)]]
        segments = [
            [make_message("system", "You are a helpful assistant"), make_message("user", "Hello", cache_control=True)],
            [make_message("user", "How are you?", cache_control=True)],
        ]

        # Mock cache hits for both segments
        def cache_get(key):
            if "seg0" in key:
                return "resp_seg0"
            if "seg1" in key:
                return "resp_seg1"
            return None

        vlm._response_cache.get.side_effect = cache_get

        # Should use seg0 cache with previous_response_id to create seg1
        mock_response = MagicMock()
        mock_response.id = "resp_combined"
        mock_client = AsyncMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        vlm.get_async_client.return_value = mock_client

        result = await vlm._get_or_create_from_segments(segments, 2)

        # Should return combined response id
        assert result == "resp_combined"

    @pytest.mark.asyncio
    async def test_two_segments_first_not_cached(self):
        """Test: Two segments, first not cached, second cached."""
        vlm = self._create_vlm_with_mock_cache()

        segments = [
            [make_message("system", "System"), make_message("user", "Hello", cache_control=True)],
            [make_message("user", "How are you?", cache_control=True)],
        ]

        # First segment not cached, second is cached
        cache_returns = {
            "prefix:seg0_system_hello": None,  # First segment - not cached
            "prefix:seg1_how_are_you": "resp_seg1",  # Second segment - cached
        }

        def cache_get(key):
            return cache_returns.get(key)

        vlm._response_cache.get.side_effect = cache_get

        # Mock API: first call creates first segment cache, second call extends with previous_response_id
        call_count = 0
        mock_responses = ["resp_seg0", "resp_combined"]

        async def mock_create(**kwargs):
            nonlocal call_count
            resp = MagicMock()
            resp.id = mock_responses[call_count]
            call_count += 1
            return resp

        mock_client = AsyncMock()
        mock_client.responses.create = mock_create
        vlm.get_async_client.return_value = mock_client

        result = await vlm._get_or_create_from_segments(segments, 2)

        # Should create first segment, then extend with second
        assert result == "resp_combined"

    @pytest.mark.asyncio
    async def test_two_segments_neither_cached(self):
        """Test: Two segments, neither cached."""
        vlm = self._create_vlm_with_mock_cache()

        segments = [
            [make_message("system", "System"), make_message("user", "Hello", cache_control=True)],
            [make_message("user", "How are you?", cache_control=True)],
        ]

        # Neither segment cached
        vlm._response_cache.get.return_value = None

        # Mock API responses
        call_count = 0
        mock_responses = ["resp_seg0", "resp_combined"]

        async def mock_create(**kwargs):
            nonlocal call_count
            resp = MagicMock()
            resp.id = mock_responses[call_count]
            call_count += 1
            return resp

        mock_client = AsyncMock()
        mock_client.responses.create = mock_create
        vlm.get_async_client.return_value = mock_client

        result = await vlm._get_or_create_from_segments(segments, 2)

        # Should create both segments
        assert result == "resp_combined"

    @pytest.mark.asyncio
    async def test_three_segments_with_middle_cached(self):
        """Test: Three segments, middle one cached, others not."""
        vlm = self._create_vlm_with_mock_cache()

        segments = [
            [make_message("system", "System"), make_message("user", "Hello", cache_control=True)],
            [make_message("user", "How are you?", cache_control=True)],
            [make_message("user", "Tell me a story", cache_control=True)],
        ]

        # Only middle segment cached
        cache_returns = {
            "prefix:seg0_system_hello": None,
            "prefix:seg1_how_are_you": "resp_seg1",  # Cached
            "prefix:seg2_tell_story": None,
        }

        def cache_get(key):
            return cache_returns.get(key)

        vlm._response_cache.get.side_effect = cache_get

        # Mock API: create seg0, extend to seg1, extend to seg2
        call_count = 0
        mock_responses = ["resp_seg0", "resp_01", "resp_012"]

        async def mock_create(**kwargs):
            nonlocal call_count
            resp = MagicMock()
            resp.id = mock_responses[call_count]
            call_count += 1
            return resp

        mock_client = AsyncMock()
        mock_client.responses.create = mock_create
        vlm.get_async_client.return_value = mock_client

        result = await vlm._get_or_create_from_segments(segments, 3)

        # Should chain: seg0 -> seg1 (cached) -> seg2
        assert result == "resp_012"

    def test_zero_segments(self):
        """Test: end_idx = 0 returns None."""
        vlm = self._create_vlm_with_mock_cache()

        segments = [[make_message("system", "System")]]

        result = VLMClass._get_or_create_from_segments(vlm, segments, 0)

        assert result is None


class TestCacheKeyGeneration:
    """Tests for cache key generation logic."""

    def test_cache_key_includes_prefix(self):
        """Test that cache keys include 'prefix:' prefix."""
        vlm = VLMClass(
            model="test-model",
            api_key="test-key",
        )

        messages = [make_message("system", "Hello")]
        key = vlm._get_response_id_cache_key(messages)

        # Should include prefix in the key
        assert "prefix:" in key or key.startswith("prefix:")