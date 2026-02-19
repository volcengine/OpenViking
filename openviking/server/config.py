# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Server configuration for OpenViking HTTP Server."""

from dataclasses import dataclass, field
from typing import List, Optional

from openviking_cli.utils.config.config_loader import (
    DEFAULT_OV_CONF,
    OPENVIKING_CONFIG_ENV,
    load_json_config,
    resolve_config_path,
)


@dataclass
class ServerConfig:
    """Server configuration (from the ``server`` section of ov.conf)."""

    host: str = "0.0.0.0"
    port: int = 1933
    api_key: Optional[str] = None
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


def load_server_config(config_path: Optional[str] = None) -> ServerConfig:
    """Load server configuration from ov.conf.

    Reads the ``server`` section of ov.conf and also ensures the full
    ov.conf is loaded into the OpenVikingConfigSingleton so that model
    and storage settings are available.

    Resolution chain:
      1. Explicit ``config_path`` (from --config)
      2. OPENVIKING_CONFIG_FILE environment variable
      3. ~/.openviking/ov.conf

    Args:
        config_path: Explicit path to ov.conf.

    Returns:
        ServerConfig instance with defaults for missing fields.

    Raises:
        FileNotFoundError: If no config file is found.
    """
    path = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if path is None:
        from openviking_cli.utils.config.config_loader import DEFAULT_CONFIG_DIR

        default_path = DEFAULT_CONFIG_DIR / DEFAULT_OV_CONF
        raise FileNotFoundError(
            f"OpenViking configuration file not found.\n"
            f"Please create {default_path} or set {OPENVIKING_CONFIG_ENV}.\n"
            f"See: https://openviking.dev/docs/guides/configuration"
        )

    data = load_json_config(path)
    server_data = data.get("server", {})

    config = ServerConfig(
        host=server_data.get("host", "0.0.0.0"),
        port=server_data.get("port", 1933),
        api_key=server_data.get("api_key"),
        cors_origins=server_data.get("cors_origins", ["*"]),
    )

    return config
