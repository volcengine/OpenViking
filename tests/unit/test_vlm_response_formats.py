# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for VLM response format handling (Issue #801)."""

from types import SimpleNamespace

import pytest

from openviking.models.vlm.base import VLMBase


class TestVLMBaseResponseFormats:
    """Test VLMBase handles various response formats correctly."""

    class ConcreteVLM(VLMBase):
        """Concrete VLM implementation for testing."""

        def get_completion(self, prompt: str, thinking: bool = False) -> str:
            pass

        async def get_completion_async(
            self, prompt: str, thinking: bool = False, max_retries: int = 0
        ) -> str:
            pass

        def get_vision_completion(
            self,
            prompt: str,
            images,
            thinking: bool = False,
        ) -> str:
            pass

        async def get_vision_completion_async(
            self,
            prompt: str,
            images,
            thinking: bool = False,
        ) -> str:
            pass

    @pytest.fixture()
    def vlm(self):
        return self.ConcreteVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
            }
        )

    def test_extract_content_from_str_response(self, vlm):
        assert vlm._extract_content_from_response("plain string response") == "plain string response"

    def test_extract_content_from_standard_openai_response(self, vlm):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="standard response content")
                )
            ]
        )
        assert vlm._extract_content_from_response(response) == "standard response content"
