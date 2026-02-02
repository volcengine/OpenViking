# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AGFSConfig(BaseModel):
    """Configuration for AGFS (Agent Global File System)."""

    path: str = Field(default="./data", description="AGFS data storage path")

    port: int = Field(default=8080, description="AGFS service port")

    log_level: str = Field(default="warn", description="AGFS log level")

    url: Optional[str] = Field(
        default="http://localhost:8080", description="AGFS service URL for service mode"
    )

    backend: str = Field(
        default="local", description="AGFS storage backend: 'local' | 's3' | 'memory'"
    )

    timeout: int = Field(default=10, description="AGFS request timeout (seconds)")

    retry_times: int = Field(default=3, description="AGFS retry times on failure")

    # S3 backend configuration
    s3_bucket: Optional[str] = Field(default=None, description="S3 bucket name")

    s3_region: Optional[str] = Field(default=None, description="S3 region")

    s3_access_key: Optional[str] = Field(default=None, description="S3 access key")

    s3_secret_key: Optional[str] = Field(default=None, description="S3 secret key")

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if self.backend not in ["local", "s3", "memory"]:
            raise ValueError(
                f"Invalid AGFS backend: '{self.backend}'. Must be one of: 'local', 's3', 'memory'"
            )

        if self.backend == "local":
            if not self.path:
                raise ValueError("AGFS local backend requires 'path' to be set")

        elif self.backend == "s3":
            missing = []
            if not self.s3_bucket:
                missing.append("s3_bucket")
            if not self.s3_region:
                missing.append("s3_region")
            if not self.s3_access_key:
                missing.append("s3_access_key")
            if not self.s3_secret_key:
                missing.append("s3_secret_key")

            if missing:
                raise ValueError(
                    f"AGFS S3 backend requires the following fields: {', '.join(missing)}"
                )

        return self
