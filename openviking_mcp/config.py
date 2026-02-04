# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Configuration management for OpenViking MCP Server."""

import os
from pathlib import Path
from typing import Optional

from openviking.utils import get_logger
from openviking.utils.config import OpenVikingConfig, get_openviking_config

logger = get_logger(__name__)


class MCPConfig:
    """Configuration for OpenViking MCP Server."""

    def __init__(self):
        """Initialize configuration from environment variables."""
        # OpenViking config file path
        self.config_file = os.environ.get("OPENVIKING_CONFIG_FILE")
        if not self.config_file:
            # Try default locations
            default_paths = [
                Path.cwd() / "ov.conf",
                Path.home() / ".openviking" / "ov.conf",
                Path("/etc/openviking/ov.conf"),
            ]
            for path in default_paths:
                if path.exists():
                    self.config_file = str(path)
                    break

        # Data directory for OpenViking storage
        self.data_dir = os.environ.get("OPENVIKING_DATA_DIR", str(Path.cwd() / "openviking_data"))

        # Log level
        self.log_level = os.environ.get("OPENVIKING_LOG_LEVEL", "INFO")

        # OpenViking configuration object
        self.openviking_config: Optional[OpenVikingConfig] = None

    def validate(self) -> tuple[bool, Optional[str]]:
        """
        Validate configuration.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self.config_file:
            return False, "OPENVIKING_CONFIG_FILE not set and no default config found"

        config_path = Path(self.config_file)
        if not config_path.exists():
            return False, f"Config file not found: {self.config_file}"

        return True, None

    def load_openviking_config(self) -> OpenVikingConfig:
        """
        Load OpenViking configuration.

        Returns:
            OpenVikingConfig: Configuration object
        """
        if self.openviking_config is None:
            try:
                self.openviking_config = get_openviking_config()
                logger.info("OpenViking configuration loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load OpenViking configuration: {e}")
                raise
        return self.openviking_config


# Global config instance
_config: Optional[MCPConfig] = None


def get_config() -> MCPConfig:
    """Get or create global config instance."""
    global _config
    if _config is None:
        _config = MCPConfig()
    return _config
