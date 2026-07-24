# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr, field_validator


class MediaModelConfig(BaseModel):
    provider: Literal["volcengine"]
    api_key: str
    model: str
    api_base: str = "https://ark.cn-beijing.volces.com/api/v3"
    timeout: float = Field(default=600.0, gt=0)
    file_processing_timeout: float = Field(default=1800.0, gt=0)
    file_poll_interval: float = Field(default=3.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    max_concurrent: int = Field(default=4, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    extra_headers: Optional[Dict[str, str]] = None

    _client_instance: Any = PrivateAttr(default=None)

    model_config = {"extra": "forbid"}

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("api_key", "model", "api_base")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    def get_client_instance(self):
        if self._client_instance is None:
            from openviking.models.media_understanding import MediaUnderstandingFactory

            self._client_instance = MediaUnderstandingFactory.create(self.model_dump())
        return self._client_instance


class VideoMediaModelConfig(MediaModelConfig):
    timeout: float = Field(default=1200.0, gt=0)
    max_concurrent: int = Field(default=2, ge=1)
    fps: float = Field(default=1.0, ge=0.2, le=5.0)


class MediaUnderstandingConfig(BaseModel):
    audio: Optional[MediaModelConfig] = None
    video: Optional[VideoMediaModelConfig] = None

    model_config = {"extra": "forbid"}
