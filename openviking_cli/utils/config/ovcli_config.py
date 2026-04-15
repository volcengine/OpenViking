# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration schema and loader for ovcli.conf."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError

from .config_loader import resolve_config_path
from .config_utils import format_validation_error
from .consts import DEFAULT_OVCLI_CONF, OPENVIKING_CLI_CONFIG_ENV


class OVCLIUploadConfig(BaseModel):
    """Upload-related defaults in ovcli.conf."""

    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None

    model_config = {"extra": "forbid"}


class OVCLIConfig(BaseModel):
    """Client configuration loaded from ovcli.conf."""

    url: Optional[str] = None
    api_key: Optional[str] = None
    agent_id: Optional[str] = None
    account: Optional[str] = None
    user: Optional[str] = None
    timeout: float = 60.0
    upload: Optional[OVCLIUploadConfig] = None

    model_config = {"extra": "forbid"}


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
