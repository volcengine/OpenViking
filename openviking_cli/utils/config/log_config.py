# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator

from openviking.observability.log_safety import DEFAULT_LOG_FORMAT, LEGACY_LOG_FORMAT


class LogConfig(BaseModel):
    """Logging configuration for OpenViking."""

    level: str = Field(
        default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )

    format: str = Field(
        default=DEFAULT_LOG_FORMAT,
        description="Common UTC log format; the former built-in format is normalized on load",
    )

    output: str = Field(default="stdout", description="Log output: stdout, stderr, or file path")

    rotation: bool = Field(default=True, description="Enable log file rotation")

    rotation_days: int = Field(default=3, description="Number of days to retain rotated log files")

    rotation_interval: str = Field(
        default="midnight",
        description="Log rotation interval: 'midnight', 'H' (hourly), 'D' (daily), 'W0'-'W6' (weekly)",
    )

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        value = value.upper()
        value = {"WARN": "WARNING", "FATAL": "CRITICAL"}.get(value, value)
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported logging level")
        return value

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: str) -> str:
        # Normalize the former built-in format when upgrading existing configurations.
        if value == LEGACY_LOG_FORMAT:
            return DEFAULT_LOG_FORMAT
        if value != DEFAULT_LOG_FORMAT:
            raise ValueError("log.format must use the common cross-runtime header")
        return value

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "LogConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
