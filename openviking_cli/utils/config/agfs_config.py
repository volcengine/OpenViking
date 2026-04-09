# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DirectoryMarkerMode(str, Enum):
    """How S3 directory markers should be persisted."""

    NONE = "none"
    EMPTY = "empty"
    NONEMPTY = "nonempty"


class S3Config(BaseModel):
    """Configuration for S3 backend."""

    bucket: Optional[str] = Field(default=None, description="S3 bucket name")

    region: Optional[str] = Field(
        default=None,
        description="AWS region where the bucket is located (e.g., us-east-1, cn-beijing)",
    )

    access_key: Optional[str] = Field(
        default=None,
        description="S3 access key ID. If not provided, RAGFS may attempt to use environment variables or IAM roles.",
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

    use_path_style: bool = Field(
        default=True,
        description="true represent UsePathStyle for MinIO and some S3-compatible services; false represent VirtualHostStyle for TOS  and some S3-compatible services.",
    )

    directory_marker_mode: DirectoryMarkerMode = Field(
        default=DirectoryMarkerMode.EMPTY,
        description="How to persist S3 directory markers: 'none' skips marker creation, 'empty' writes a zero-byte marker, and 'nonempty' writes a non-empty marker payload. Defaults to 'empty'.",
    )

    disable_batch_delete: bool = Field(
        default=False,
        description="Disable batch delete (DeleteObjects) and use sequential single-object deletes instead. "
        "Required for S3-compatible services like Alibaba Cloud OSS that require a Content-MD5 header "
        "for DeleteObjects but AWS SDK v2 does not send it by default. Defaults to False.",
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
    """Configuration for RAGFS (Rust-based AGFS)."""

    path: Optional[str] = Field(
        default=None,
        description="[Deprecated in favor of `storage.workspace`] RAGFS data storage path. This will be ignored if `storage.workspace` is set.",
    )

    backend: str = Field(
        default="local", description="RAGFS storage backend: 'local' | 's3' | 'memory'"
    )

    timeout: int = Field(default=10, description="RAGFS request timeout (seconds)")

    # S3 backend configuration
    # These settings are used when backend is set to 's3'.
    # RAGFS will act as a gateway to the specified S3 bucket.
    s3: S3Config = Field(default_factory=lambda: S3Config(), description="S3 backend configuration")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if self.backend not in ["local", "s3", "memory"]:
            raise ValueError(
                f"Invalid RAGFS backend: '{self.backend}'. Must be one of: 'local', 's3', 'memory'"
            )

        if self.backend == "local":
            pass

        elif self.backend == "s3":
            # Validate S3 configuration
            self.s3.validate_config()

        return self
