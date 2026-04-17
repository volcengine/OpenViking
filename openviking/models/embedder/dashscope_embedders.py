# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DashScope embedding implementations.

This PR intentionally supports only dense text embeddings through DashScope's
OpenAI-compatible endpoint. Native multimodal embedding support is deferred.
"""

from typing import Any, Dict, Optional

from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder


class DashScopeDenseEmbedder(OpenAIDenseEmbedder):
    """DashScope dense embedder via the OpenAI-compatible embeddings API."""

    DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    supports_multimodal: bool = False

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        input_type: Optional[str] = "text",
    ):
        normalized_input_type = (input_type or "text").lower()
        if normalized_input_type != "text":
            raise ValueError(
                "DashScope currently supports dense text embeddings only. "
                "input='multimodal' is not supported in this PR."
            )

        super().__init__(
            model_name=model_name,
            api_key=api_key,
            api_base=api_base or self.DEFAULT_API_BASE,
            dimension=dimension,
            query_param=query_param,
            document_param=document_param,
            config=config,
            extra_headers=extra_headers,
            provider="openai",
            configured_provider="dashscope",
        )

        # Keep transport behavior OpenAI-compatible while reporting the configured provider.
        self._provider = "dashscope"
        self.provider = "dashscope"
        self.input_type = normalized_input_type

    def _should_send_dimensions(self) -> bool:
        # Stay conservative with DashScope's compatible endpoint and preserve the same
        # request shape as the existing provider='openai' workaround.
        return False
