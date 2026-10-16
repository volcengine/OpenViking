# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration for embedded and standalone gateway hosts."""

import logging
import os
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

ENVIRONMENT_PREFIX = "OPENVIKING_GATEWAY_"
PUBLIC_OPENVIKING_URLS = frozenset({"https://api.vikingdb.cn-beijing.volces.com/openviking"})
# Both hosts mount the gateway here; public_url is the origin in front of it.
MOUNT_PATH = "/gateway"


def normalize_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        # Reading the port also rejects malformed ones.
        or parsed.port == 0
    ):
        raise ValueError("expected an HTTP(S) URL without credentials, query or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Settings an upgraded configuration may still carry; rejecting them would stop the host.
    removed_fields: ClassVar[frozenset[str]] = frozenset({"encryption_key_env"})

    public_url: str = ""
    storage_path: str = "~/.openviking/gateway"
    management_key: str = Field(default="", repr=False)
    min_server_version: str = "0.4.16"
    session_ttl_days: int = Field(default=30, ge=1)
    response_ttl_seconds: int = Field(default=30 * 86400, ge=60)
    log_retention_days: int = Field(default=30, ge=1)
    max_body_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    upstream_timeout_seconds: float = Field(default=600, gt=0)
    health_interval_seconds: float = Field(default=60, ge=1)

    @model_validator(mode="before")
    @classmethod
    def apply_environment(cls, value):
        if not isinstance(value, dict):
            return value
        if found := cls.removed_fields.intersection(value):
            logger.warning(
                "Ignoring removed gateway settings %s. "
                "See docs/en/guides/22-gateway-operations.md#migration",
                ", ".join(sorted(found)),
            )
        value = {key: item for key, item in value.items() if key not in found}
        for field in cls.model_fields:
            # An empty variable, such as a compose default, never overrides the file.
            override = os.environ.get(ENVIRONMENT_PREFIX + field.upper(), "")
            # Kubernetes service links set OPENVIKING_GATEWAY_PORT=tcp://... for such a Service.
            if override and not override.startswith(("tcp://", "udp://")):
                value[field] = override
        return value

    @field_validator("public_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_url(value) if value else ""

    @field_validator("management_key")
    @classmethod
    def validate_management_key(cls, value: str) -> str:
        value = os.path.expandvars(value)
        if value and (len(value) < 32 or value.startswith("$")):
            raise ValueError("management_key must resolve to at least 32 characters")
        return value

    @property
    def directory(self) -> Path:
        return Path(self.storage_path).expanduser()


class EmbeddedGatewayConfig(GatewayConfig):
    # v0.5.0 sidecar settings: the gateway now runs inside the server on the server port.
    removed_fields = GatewayConfig.removed_fields | {
        "host",
        "port",
        "workers",
        "url",
        "openviking_url",
        "admin_token_env",
    }

    enabled: bool = False


class StandaloneGatewayConfig(GatewayConfig):
    openviking_url: str = Field(min_length=1)
    host: str = "127.0.0.1"
    port: int = Field(default=1935, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, le=64)
    # Same schema as ov.conf's top-level "encryption"; absent or disabled stores plaintext.
    encryption: dict | None = Field(default=None, repr=False)

    @field_validator("openviking_url")
    @classmethod
    def validate_openviking_url(cls, value: str) -> str:
        return normalize_url(value)

    @field_validator("encryption")
    @classmethod
    def validate_encryption(cls, value: dict | None) -> dict | None:
        # Imported here because the server's config package imports this module.
        from openviking_cli.utils.config.encryption_config import EncryptionConfig

        return None if value is None else EncryptionConfig.model_validate(value).model_dump()
