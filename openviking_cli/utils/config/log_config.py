# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict

from pydantic import BaseModel, Field


class LogConfig(BaseModel):
    """Logging configuration for OpenViking."""

    level: str = Field(
        default="WARNING", description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )

    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string",
    )

    output: str = Field(default="stdout", description="Log output: stdout, stderr, or file path")

    rotation: bool = Field(default=True, description="Enable log file rotation")

    rotation_days: int = Field(default=3, description="Number of days to retain rotated log files")

    rotation_interval: str = Field(
        default="midnight",
        description="Log rotation interval: 'midnight', 'H' (hourly), 'D' (daily), 'W0'-'W6' (weekly)",
    )

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "LogConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
