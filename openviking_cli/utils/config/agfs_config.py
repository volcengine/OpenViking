# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class S3Config(BaseModel):
    """Configuration for S3 backend."""

    bucket: Optional[str] = Field(default=None, description="S3 bucket name")

    region: Optional[str] = Field(
        default=None,
        description="AWS region where the bucket is located (e.g., us-east-1, cn-beijing)",
    )

    access_key: Optional[str] = Field(
        default=None,
        description="S3 access key ID. If not provided, AGFS may attempt to use environment variables or IAM roles.",
    )

    secret_key: Optional[str] = Field(
        default=None,
        description="S3 secret access key corresponding to the access key ID.",
    )

    endpoint: Optional[str] = Field(
        default=None,
        description="Custom S3 endpoint URL. Required for S3-compatible services like MinIO or LocalStack. "
        "Leave empty for standard AWS S3.",
    )

    prefix: Optional[str] = Field(
        default="",
        description="Optional key prefix for namespace isolation. All objects will be stored under this prefix.",
    )

    use_ssl: bool = Field(
        default=True,
        description="Enable/Disable SSL (HTTPS) for S3 connections. Set to False for local testing without HTTPS.",
    )

    model_config = {"extra": "forbid"}

    def validate_config(self):
        """Validate S3 configuration completeness"""
        missing = []
        if not self.bucket:
            missing.append("bucket")
        if not self.endpoint:
            missing.append("endpoint")
        if not self.region:
            missing.append("region")
        if not self.access_key:
            missing.append("access_key")
        if not self.secret_key:
            missing.append("secret_key")

        if missing:
            raise ValueError(f"S3 backend requires the following fields: {', '.join(missing)}")

        return self


class AGFSConfig(BaseModel):
    """Configuration for AGFS (Agent Global File System)."""

    path: str = Field(default="./data", description="AGFS data storage path")

    port: int = Field(default=1833, description="AGFS service port")

    log_level: str = Field(default="warn", description="AGFS log level")

    url: Optional[str] = Field(
        default="http://localhost:1833", description="AGFS service URL for service mode"
    )

    backend: str = Field(
        default="local", description="AGFS storage backend: 'local' | 's3' | 'memory'"
    )

    timeout: int = Field(default=10, description="AGFS request timeout (seconds)")

    retry_times: int = Field(default=3, description="AGFS retry times on failure")

    use_ssl: bool = Field(
        default=True,
        description="Enable/Disable SSL (HTTPS) for AGFS service. Set to False for local testing without HTTPS.",
    )

    # S3 backend configuration
    # These settings are used when backend is set to 's3'.
    # AGFS will act as a gateway to the specified S3 bucket.
    s3: S3Config = Field(default_factory=lambda: S3Config(), description="S3 backend configuration")

    model_config = {"extra": "forbid"}

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
            # Validate S3 configuration
            self.s3.validate_config()

        return self
