# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Git version control configuration for OpenViking."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class GitLocalConfig(BaseModel):
    """Configuration for the local git object backend."""

    base_dir: str = Field(
        default="",
        description="Filesystem directory holding git objects/refs. "
        "When empty, defaults to '{storage.path}/.ovgit'.",
    )

    model_config = {"extra": "forbid"}


class GitS3Config(BaseModel):
    """Configuration for the S3 git object/ref backend.

    Fields mirror the Rust ``GitS3ConfigPy`` serde struct so that the rendered
    ``[git.s3]`` TOML section is consumed verbatim by the ragfs binding.
    """

    bucket: str = Field(
        default="",
        description="S3 bucket name holding git objects/refs.",
    )
    region: str = Field(
        default="us-east-1",
        description="AWS region where the bucket is located (e.g., us-east-1, cn-beijing).",
    )
    prefix: str = Field(
        default=".ovgit",
        description="Key prefix for git storage. All keys are stored under '{prefix}/{account}/...'.",
    )
    endpoint: str = Field(
        default="",
        description="Custom S3 endpoint URL for S3-compatible services like MinIO/LocalStack/TOS. "
        "Leave empty for standard AWS S3.",
    )
    access_key: Optional[str] = Field(
        default=None,
        description="S3 access key ID read directly from config. "
        "When empty, the SDK default credentials chain is used.",
    )
    secret_key: Optional[str] = Field(
        default=None,
        description="S3 secret access key read directly from config. "
        "When empty, the SDK default credentials chain is used.",
    )
    cas_mode: Literal["native"] = Field(
        default="native",
        description="Ref CAS mode. 'native' uses S3 conditional writes (If-Match). "
        "It is the only supported mode.",
    )
    use_path_style: bool = Field(
        default=True,
        description="true uses path-style addressing (MinIO and some S3-compatible services); "
        "false uses virtual-host style (TOS and some S3-compatible services).",
    )

    model_config = {"extra": "forbid"}


class GitConfig(BaseModel):
    """Git multi-version management configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable git-based multi-version management for VikingFS content.",
    )
    backend: Literal["local", "s3"] = Field(
        default="local",
        description="Git object backend. 'local' stores objects on the local filesystem; "
        "'s3' stores them on a remote S3-compatible bucket. When unset, defaults to "
        "the same backend as 'storage.agfs.backend' (a 'memory' storage backend maps "
        "to 'local').",
    )
    default_branch: str = Field(
        default="main",
        description="Default branch name for commits when not specified.",
    )
    author_name: str = Field(
        default="viking-bot",
        description="Default author name used when callers omit author_name.",
    )
    author_email: str = Field(
        default="bot@viking.local",
        description="Default author email used when callers omit author_email.",
    )
    local: GitLocalConfig = Field(
        default_factory=GitLocalConfig,
        description="Configuration for the 'local' backend.",
    )
    s3: Optional[GitS3Config] = Field(
        default=None,
        description="Configuration for the 's3' backend. Required when backend='s3'.",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_backend(self) -> "GitConfig":
        """Ensure the selected backend has the required configuration."""
        if self.enabled and self.backend == "s3":
            if self.s3 is None:
                raise ValueError("git backend 's3' requires a [git.s3] section")
            missing = []
            if not self.s3.bucket:
                missing.append("bucket")
            if not self.s3.region:
                missing.append("region")
            if missing:
                raise ValueError(
                    f"git backend 's3' requires the following fields: {', '.join(missing)}"
                )
        return self
