# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration for embedded and standalone gateway hosts."""

import json
import logging
import os
import re
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Policy, Upstream

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


class RuleChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    add: list[str] = Field(default_factory=list)
    # Entries of the packaged list, written exactly as there.
    remove: list[str] = Field(default_factory=list)


class ClientRules(BaseModel):
    """Changes to the packaged ``client-rules.json``, applied when the gateway starts."""

    model_config = ConfigDict(extra="forbid")
    plugin_tags: RuleChanges = Field(default_factory=RuleChanges)
    noise_tags: RuleChanges = Field(default_factory=RuleChanges)
    noise_patterns: RuleChanges = Field(default_factory=RuleChanges)
    auxiliary_patterns: RuleChanges = Field(default_factory=RuleChanges)

    @field_validator("plugin_tags", "noise_tags")
    @classmethod
    def validate_tags(cls, value: RuleChanges) -> RuleChanges:
        # Element names, as clients write them in <name ...>.
        if bad := [tag for tag in value.add if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", tag)]:
            raise ValueError(f"tag names must be ASCII letters, digits, _ . : or -: {bad}")
        return value

    @field_validator("noise_patterns", "auxiliary_patterns")
    @classmethod
    def validate_patterns(cls, value: RuleChanges) -> RuleChanges:
        for pattern in value.add:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"invalid regex {pattern!r}: {error}") from None
        return value


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kernel: Literal["sqlite", "redis"] = "sqlite"
    management: Literal["sqlite", "redis"] = "sqlite"
    # Keep hash tags and SCAN patterns independent of the configured namespace.
    key_prefix: str = Field(default="ovgw:", pattern=r"^[^{}*?\[\]\\]*$")
    redis: dict = Field(default_factory=dict, repr=False, validate_default=True)

    @field_validator("redis")
    @classmethod
    def validate_redis(cls, value: dict) -> dict:
        from openviking_cli.utils.config.agfs_config import RedisCacheConfig

        if unknown := set(value) - set(RedisCacheConfig.model_fields):
            raise ValueError(f"unknown redis settings: {', '.join(sorted(unknown))}")
        return RedisCacheConfig.model_validate({"command_timeout_ms": 5000, **value}).model_dump()


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Settings an upgraded configuration may still carry; rejecting them would stop the host.
    removed_fields: ClassVar[frozenset[str]] = frozenset({"encryption_key_env", "session_ttl_days"})

    public_url: str = ""
    mcp_file_links: Literal["proxy", "direct"] = "proxy"
    storage_path: str = "~/.openviking/gateway"
    management_api: bool = True
    storage: StorageConfig = Field(default_factory=StorageConfig)
    management_key: str = Field(default="", repr=False)
    # Accept OpenViking API keys in place of gateway keys, with the account's defaults.
    openviking_keys: bool = False
    tenancy: Literal["account", "key"] = "account"
    # Used where an account stores none: OpenViking keys and the "default" profile.
    default_upstreams: dict[str, Upstream] = Field(default_factory=dict, repr=False)
    default_profile: Policy = Field(default_factory=Policy)
    min_server_version: str = "0.4.16"
    # The 3600 floor keeps sessions alive past capture's default 600-second idle tail.
    session_ttl_seconds: int = Field(default=30 * 86400, ge=3600)
    response_ttl_seconds: int = Field(default=30 * 86400, ge=60)
    request_log_sink: Literal["store", "stdout"] = "store"
    log_retention_days: int = Field(default=30, ge=1)
    max_body_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    upstream_timeout_seconds: float = Field(default=600, gt=0)
    health_interval_seconds: float = Field(default=60, ge=1)
    capture_concurrency: int = Field(default=1, ge=1)
    client_rules: ClientRules = Field(default_factory=ClientRules)

    @model_validator(mode="after")
    def validate_tenancy(self):
        if self.tenancy == "key" and self.management_api:
            raise ValueError("tenancy 'key' requires management_api: false")
        return self

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

    @field_validator(
        "client_rules", "storage", "default_upstreams", "default_profile", mode="before"
    )
    @classmethod
    def parse_json(cls, value):
        # OPENVIKING_GATEWAY_<FIELD> carries these sections as JSON.
        return json.loads(value) if isinstance(value, str) else value

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
    keep_alive_seconds: float = Field(default=75, gt=0)
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
