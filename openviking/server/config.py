# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Server configuration for OpenViking HTTP Server."""

import sys
from dataclasses import dataclass, field
from typing import List, Optional

from openviking_cli.utils import get_logger
from openviking_cli.utils.config.config_loader import (
    DEFAULT_OV_CONF,
    OPENVIKING_CONFIG_ENV,
    load_json_config,
    resolve_config_path,
)

logger = get_logger(__name__)


@dataclass
class ServerConfig:
    """Server configuration (from the ``server`` section of ov.conf)."""

    host: str = "127.0.0.1"
    port: int = 1933
    root_api_key: Optional[str] = None
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
        host=server_data.get("host", "127.0.0.1"),
        port=server_data.get("port", 1933),
        root_api_key=server_data.get("root_api_key"),
        cors_origins=server_data.get("cors_origins", ["*"]),
    )

    return config


_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_localhost(host: str) -> bool:
    """Return True if *host* resolves to a loopback address."""
    return host in _LOCALHOST_HOSTS


def validate_server_config(config: ServerConfig) -> None:
    """Validate server config for safe startup.

    When ``root_api_key`` is not set, authentication is disabled (dev mode).
    This is only acceptable when the server binds to localhost.  Binding to a
    non-loopback address without authentication exposes an unauthenticated ROOT
    endpoint to the network.

    Raises:
        SystemExit: If the configuration is unsafe.
    """
    if config.root_api_key:
        return

    if not _is_localhost(config.host):
        logger.error(
            "SECURITY: server.root_api_key is not configured and server.host "
            "is '%s' (non-localhost). This would expose an unauthenticated "
            "ROOT endpoint to the network.",
            config.host,
        )
        logger.error(
            "To fix, either:\n"
            "  1. Set server.root_api_key in ov.conf, or\n"
            '  2. Bind to localhost (server.host = "127.0.0.1")'
        )
        sys.exit(1)
