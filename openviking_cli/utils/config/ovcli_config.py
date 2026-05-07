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
    extra_headers: Optional[Dict[str, str]] = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def handle_extra_headers_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 支持 extra_header 作为 extra_headers 的别名
            if "extra_header" in data and "extra_headers" not in data:
                # 复制字典并移除 extra_header，避免 extra: "forbid" 报错
                new_data = {k: v for k, v in data.items() if k != "extra_header"}
                new_data["extra_headers"] = data["extra_header"]
                data = new_data
            elif "extra_headers" in data and "extra_header" in data:
                # 优先使用 extra_headers，移除 extra_header
                data = {k: v for k, v in data.items() if k != "extra_header"}
        return data


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
