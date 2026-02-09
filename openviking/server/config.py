# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Server configuration for OpenViking HTTP Server."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ServerConfig:
    """Server configuration."""

    host: str = "0.0.0.0"
    port: int = 1933
    api_key: Optional[str] = None
    storage_path: Optional[str] = None
    vectordb_url: Optional[str] = None
    agfs_url: Optional[str] = None
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


def load_server_config(config_path: Optional[str] = None) -> ServerConfig:
    """Load server configuration from file and environment variables.

    Priority: command line args > environment variables > config file

    Config file lookup:
        1. Explicit config_path (from --config)
        2. OPENVIKING_CONFIG_FILE environment variable

    Args:
        config_path: Path to config file.

    Returns:
        ServerConfig instance
    """
    config = ServerConfig()

    # Load from config file
    if config_path is None:
        config_path = os.environ.get("OPENVIKING_CONFIG_FILE")

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = json.load(f) or {}

        server_data = data.get("server", {})
        config.host = server_data.get("host", config.host)
        config.port = server_data.get("port", config.port)
        config.api_key = server_data.get("api_key", config.api_key)
        config.cors_origins = server_data.get("cors_origins", config.cors_origins)

        storage_data = data.get("storage", {})
        config.storage_path = storage_data.get("path", config.storage_path)
        config.vectordb_url = storage_data.get("vectordb_url", config.vectordb_url)
        config.agfs_url = storage_data.get("agfs_url", config.agfs_url)

    # Override with environment variables
    if os.environ.get("OPENVIKING_HOST"):
        config.host = os.environ["OPENVIKING_HOST"]
    if os.environ.get("OPENVIKING_PORT"):
        config.port = int(os.environ["OPENVIKING_PORT"])
    if os.environ.get("OPENVIKING_API_KEY"):
        config.api_key = os.environ["OPENVIKING_API_KEY"]
    if os.environ.get("OPENVIKING_PATH"):
        config.storage_path = os.environ["OPENVIKING_PATH"]
    if os.environ.get("OPENVIKING_VECTORDB_URL"):
        config.vectordb_url = os.environ["OPENVIKING_VECTORDB_URL"]
    if os.environ.get("OPENVIKING_AGFS_URL"):
        config.agfs_url = os.environ["OPENVIKING_AGFS_URL"]

    return config
