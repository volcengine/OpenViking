# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration schema and loader for ovcli.conf."""

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ValidationError, model_validator

from .config_loader import resolve_config_path
from .config_utils import format_validation_error
from .consts import DEFAULT_OVCLI_CONF, OPENVIKING_CLI_CONFIG_ENV


class OVCLIUploadConfig(BaseModel):
    """Upload-related defaults in ovcli.conf."""

    mode: Optional[str] = None
    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None

    model_config = {"extra": "forbid"}


class OVCLIConfig(BaseModel):
    """Client configuration loaded from ovcli.conf.

    The file's schema belongs to the Rust CLI (``crates/ov_cli/src/config.rs``),
    which writes it. Every field it can write is accepted here, including the
    ones this loader never reads, so one file serves both.
    """

    url: Optional[str] = None
    api_key: Optional[str] = None
    root_api_key: Optional[str] = None
    account: Optional[str] = None
    user: Optional[str] = None
    actor_peer_id: Optional[str] = None
    agent_id: Optional[str] = None
    timeout: float = 60.0
    profile: bool = False
    output: Optional[str] = None
    echo_command: Optional[bool] = None
    show_progress: Optional[bool] = None
    verbose: Optional[bool] = None
    upload: Optional[OVCLIUploadConfig] = None
    extra_headers: Optional[Dict[str, str]] = None
    gateway_token: Optional[str] = None
    # Client-side harness plugin settings, owned by the memory plugins. Kept
    # opaque so a harness can add its own knobs without touching this schema.
    plugin: Optional[Dict[str, Any]] = None

    # Authentication mode: "api_key", "ldap", "oidc"
    auth_mode: Optional[str] = None
    # LDAP credentials
    ldap_username: Optional[str] = None
    ldap_password: Optional[str] = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def handle_extra_headers_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            extra_header = data.pop("extra_header", None)
            if extra_header is not None and "extra_headers" not in data:
                data["extra_headers"] = extra_header
        return data

    @model_validator(mode="after")
    def reject_mixed_actor_and_agent_identity(self) -> "OVCLIConfig":
        if self.actor_peer_id is not None and self.agent_id is not None:
            raise ValueError("actor_peer_id cannot be used with agent_id")

        # auth_mode must be one of the supported types
        valid_auth_modes = {None, "api_key", "ldap", "oidc"}
        if self.auth_mode not in valid_auth_modes:
            raise ValueError(
                f"auth_mode must be one of: "
                f"{', '.join(str(m) for m in valid_auth_modes if m is not None)}"
            )

        # In ldap mode, username is required (user field may serve as an alias)
        if self.auth_mode == "ldap" and not self.ldap_username and not self.user:
            pass

        return self


def load_ovcli_config(config_path: Optional[str] = None) -> Optional[OVCLIConfig]:
    """Load ovcli.conf if present and validate it strictly."""
    path = resolve_config_path(config_path, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
    if path is None:
        return None

    try:
        from .config_loader import load_json_config

        data = load_json_config(Path(path))
        return OVCLIConfig.model_validate(data)
    except ValidationError as e:
        raise ValueError(
            f"Invalid CLI config in {path}:\n"
            f"{format_validation_error(root_model=OVCLIConfig, error=e, path_prefix='ovcli')}"
        ) from e
