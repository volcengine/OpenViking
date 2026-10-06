# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration shared by the standalone gateway and the server admin proxy."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=1935, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, le=64)
    url: str = "http://127.0.0.1:1935"
    openviking_url: str = "http://127.0.0.1:1933"
    public_url: str = ""
    storage_path: str = "~/.openviking/context-gateway"
    encryption_key_env: str = "OPENVIKING_CONTEXT_GATEWAY_ENCRYPTION_KEY"
    admin_token_env: str = "OPENVIKING_CONTEXT_GATEWAY_ADMIN_TOKEN"
    min_server_version: str = "0.4.16"
    session_ttl_days: int = Field(default=30, ge=1)
    response_ttl_seconds: int = Field(default=30 * 86400, ge=60)
    log_retention_days: int = Field(default=30, ge=1)
    max_body_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    upstream_timeout_seconds: float = Field(default=600, gt=0)
    health_interval_seconds: float = Field(default=60, ge=1)

    @field_validator("url", "openviking_url", "public_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("expected an HTTP(S) URL without credentials, query or fragment")
        return value.rstrip("/")

    def secrets(self) -> tuple[str, str]:
        key = os.environ.get(self.encryption_key_env, "")
        token = os.environ.get(self.admin_token_env, "")
        if not key or len(token) < 32:
            raise ValueError(
                "configure a Fernet encryption key and an admin token of at least 32 characters"
            )
        return key, token

    @property
    def directory(self) -> Path:
        return Path(self.storage_path).expanduser()
