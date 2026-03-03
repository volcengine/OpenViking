# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
AGFS Client utilities for creating and configuring AGFS clients.
"""

import os
from pathlib import Path
from typing import Any

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def create_agfs_client(agfs_config: Any) -> Any:
    """
    Create an AGFS client based on the provided configuration.

    Args:
        agfs_config: AGFS configuration object containing mode and other settings.

    Returns:
        An AGFSClient or AGFSBindingClient instance.
    """
    # Ensure agfs_config is not None
    if agfs_config is None:
        raise ValueError("agfs_config cannot be None")
    mode = getattr(agfs_config, "mode", "http-client")

    if mode == "binding-client":
        # Import binding client if mode is binding-client
        try:
            from pyagfs import AGFSBindingClient
        except ImportError:
            raise ImportError(
                "AGFS binding client not found. Please run 'pip install -e .' in the project root to install the AGFS SDK."
            )

        lib_path = getattr(agfs_config, "lib_path", None)
        if lib_path and lib_path not in ["1", "default"]:
            os.environ["AGFS_LIB_PATH"] = lib_path
        else:
            os.environ["AGFS_LIB_PATH"] = str(Path(__file__).parent.parent / "lib")

        # Check if binding library exists
        try:
            from pyagfs.binding_client import _find_library

            actual_lib_path = _find_library()
        except Exception:
            raise ImportError(
                "AGFS binding library not found. Please run 'pip install -e .' in the project root to build and install the AGFS SDK."
            )

        client = AGFSBindingClient()
        logger.debug(f"[AGFSUtils] Created AGFSBindingClient (lib_path={actual_lib_path})")

        # Automatically mount backend for binding client
        mount_agfs_backend(client, agfs_config)

        return client
    else:
        # Default to http-client
        from pyagfs import AGFSClient

        url = getattr(agfs_config, "url", "http://localhost:8080")
        timeout = getattr(agfs_config, "timeout", 10)
        client = AGFSClient(api_base_url=url, timeout=timeout)
        logger.info(f"[AGFSUtils] Created AGFSClient at {url}")
        return client


def mount_agfs_backend(agfs: Any, agfs_config: Any) -> None:
    """
    Mount backend filesystem for an AGFS client based on configuration.

    Args:
        agfs: AGFS client instance (HTTP or Binding).
        agfs_config: AGFS configuration object containing backend settings.
    """
    from pyagfs import AGFSBindingClient

    from openviking.agfs_manager import AGFSManager

    # Only binding-client needs manual mounting, but we can also do it for HTTP client
    # if it supports it. Usually, HTTP server handles its own mounting.
    if not isinstance(agfs, AGFSBindingClient):
        return

    # 1. Mount standard plugins to align with HTTP server behavior
    agfs_manager = AGFSManager(agfs_config)
    config = agfs_manager._generate_config()

    for plugin_name, plugin_config in config["plugins"].items():
        mount_path = plugin_config["path"]
        # Ensure localfs directory exists before mounting
        if plugin_name == "localfs" and "local_dir" in plugin_config.get("config", {}):
            local_dir = plugin_config["config"]["local_dir"]
            os.makedirs(local_dir, exist_ok=True)
            logger.debug(f"[AGFSUtils] Ensured local directory exists: {local_dir}")

        try:
            agfs.unmount(mount_path)
        except Exception:
            pass
        try:
            agfs.mount(plugin_name, mount_path, plugin_config.get("config", {}))
            logger.debug(f"[AGFSUtils] Successfully mounted {plugin_name} at {mount_path}")
        except Exception as e:
            logger.error(f"[AGFSUtils] Failed to mount {plugin_name} at {mount_path}: {e}")
