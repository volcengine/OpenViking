# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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
        # Use get_binding_client() to respect RAGFS_IMPL env var > config.impl > "auto"
        from openviking.pyagfs import get_binding_client

        config_impl = getattr(agfs_config, "impl", "auto")
        env_impl = os.environ.get("RAGFS_IMPL", "").lower() or None
        effective_impl = env_impl or config_impl or "auto"
        AGFSBindingClient, _ = get_binding_client(config_impl)

        if AGFSBindingClient is None:
            raise ImportError(
                "AGFS binding client is not available. The native library (libagfsbinding) "
                "could not be loaded. Please run 'pip install -e .' in the project root "
                "to build and install the AGFS SDK with native bindings."
            )

        # Go ctypes binding needs AGFS_LIB_PATH and a shared library on disk.
        # Rust PyO3 binding is compiled into ragfs_python — skip library checks.
        try:
            from openviking.pyagfs.binding_client import (
                AGFSBindingClient as _GoBindingClient,
            )

            is_go_binding = AGFSBindingClient is _GoBindingClient
        except (ImportError, OSError):
            is_go_binding = False

        if is_go_binding:
            lib_path = getattr(agfs_config, "lib_path", None)
            if lib_path and lib_path not in ["1", "default"]:
                os.environ["AGFS_LIB_PATH"] = lib_path
            else:
                os.environ["AGFS_LIB_PATH"] = str(Path(__file__).parent.parent / "lib")

            try:
                from openviking.pyagfs.binding_client import _find_library

                _find_library()
            except Exception:
                raise ImportError(
                    "AGFS binding library not found. Please run 'pip install -e .' in the project root to build and install the AGFS SDK."
                )

        client = AGFSBindingClient()
        binding_type = "Rust (ragfs-python)" if not is_go_binding else "Go (libagfsbinding)"
        logger.warning(
            f"[AGFS] Binding impl selected: {binding_type} "
            f"(RAGFS_IMPL={effective_impl}, env={env_impl}, config={config_impl})"
        )

        # Automatically mount backend for binding client
        mount_agfs_backend(client, agfs_config)

        return client
    else:
        # Default to http-client
        from openviking.pyagfs import AGFSClient

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
    from openviking.agfs_manager import AGFSManager

    # Only binding-client needs manual mounting. HTTP server handles its own mounting.
    # Check for the presence of a `mount` method as the duck-type indicator for
    # binding clients (works for both Rust and Go implementations).
    if not callable(getattr(agfs, "mount", None)):
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
        # Ensure queuefs db_path parent directory exists before mounting
        if plugin_name == "queuefs" and "db_path" in plugin_config.get("config", {}):
            db_path = plugin_config["config"]["db_path"]
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

        try:
            agfs.unmount(mount_path)
        except Exception:
            pass
        try:
            agfs.mount(plugin_name, mount_path, plugin_config.get("config", {}))
            logger.debug(f"[AGFSUtils] Successfully mounted {plugin_name} at {mount_path}")
        except Exception as e:
            logger.error(f"[AGFSUtils] Failed to mount {plugin_name} at {mount_path}: {e}")
