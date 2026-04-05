# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration models for custom parser registration."""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class CustomParserConfig(BaseModel):
    """Config for a single custom parser entry in ``ov.conf``."""

    class_path: str = Field(alias="class")
    extensions: list[str]
    kwargs: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid", "populate_by_name": True}

    @field_validator("class_path")
    @classmethod
    def _validate_class_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("class must not be empty")
        if "." not in value:
            raise ValueError("class must be a fully qualified import path")
        return value

    @field_validator("extensions")
    @classmethod
    def _normalize_extensions(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("extensions must not be empty")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("extensions must contain strings")
            ext = item.strip().lower()
            if not ext:
                raise ValueError("extensions must not contain empty values")
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in seen:
                normalized.append(ext)
                seen.add(ext)
        return normalized
