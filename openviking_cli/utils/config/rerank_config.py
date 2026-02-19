# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Optional

from pydantic import BaseModel, Field


class RerankConfig(BaseModel):
    """Configuration for VikingDB Rerank API."""

    ak: Optional[str] = Field(default=None, description="VikingDB Access Key")
    sk: Optional[str] = Field(default=None, description="VikingDB Secret Key")
    host: str = Field(
        default="api-vikingdb.vikingdb.cn-beijing.volces.com", description="VikingDB API host"
    )
    model_name: str = Field(default="doubao-seed-rerank", description="Rerank model name")
    model_version: str = Field(default="251028", description="Rerank model version")
    threshold: float = Field(
        default=0.1, description="Relevance threshold (score > threshold is relevant)"
    )

    model_config = {"extra": "forbid"}

    def is_available(self) -> bool:
        """Check if rerank is configured."""
        return self.ak is not None and self.sk is not None
