# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
RAGFS Client utilities for creating and configuring RAGFS clients.
"""

import os
from pathlib import Path
from typing import Any, Dict

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _generate_plugin_config(agfs_config: Any, data_path: Path) -> Dict[str, Any]:
    """Dynamically generate RAGFS plugin configuration based on backend type."""
    config = {
        "serverinfofs": {
            "enabled": True,
            "path": "/serverinfo",
            "config": {
                "version": "1.0.0",
            },
        },
        "queuefs": {
            "enabled": True,
            "path": "/queue",
            "config": {
                "backend": "sqlite",
                "db_path": str(data_path / "_system" / "queue" / "queue.db"),
            },
        },
    }

    backend = getattr(agfs_config, "backend", "local")
    s3_config = getattr(agfs_config, "s3", None)
    vikingfs_path = data_path / "viking"

    if backend == "local":
        config["localfs"] = {
            "enabled": True,
            "path": "/local",
            "config": {
                "local_dir": str(vikingfs_path),
            },
        }
    elif backend == "s3" and s3_config:
        s3_plugin_config = {
            "bucket": s3_config.bucket,
            "region": s3_config.region,
            "access_key_id": s3_config.access_key,
            "secret_access_key": s3_config.secret_key,
            "endpoint": s3_config.endpoint,
            "prefix": s3_config.prefix,
            "disable_ssl": not s3_config.use_ssl,
            "use_path_style": s3_config.use_path_style,
            "directory_marker_mode": s3_config.directory_marker_mode.value
            if hasattr(s3_config.directory_marker_mode, "value")
            else s3_config.directory_marker_mode,
            "disable_batch_delete": s3_config.disable_batch_delete,
        }

        config["s3fs"] = {
            "enabled": True,
            "path": "/local",
            "config": s3_plugin_config,
        }
    elif backend == "memory":
        config["memfs"] = {
            "enabled": True,
            "path": "/local",
        }
    return config


def create_agfs_client(agfs_config: Any) -> Any:
    """
    Create a RAGFS client based on the provided configuration.

    Args:
        agfs_config: RAGFS configuration object.

    Returns:
        A RAGFSBindingClient instance.
    """
    # Ensure agfs_config is not None
    if agfs_config is None:
        raise ValueError("agfs_config cannot be None")

    # Import binding client
    from openviking.pyagfs import get_binding_client

    RAGFSBindingClient, _ = get_binding_client()

    if RAGFSBindingClient is None:
        raise ImportError(
            "RAGFS binding client is not available. The native library (ragfs_python) "
            "could not be loaded. Please run 'pip install -e .' in the project root "
            "to build and install the RAGFS SDK with native bindings."
        )

    client = RAGFSBindingClient()

    # Automatically mount backend for binding client
    mount_agfs_backend(client, agfs_config)

    return client


def mount_agfs_backend(agfs: Any, agfs_config: Any) -> None:
    """
    Mount backend filesystem for a RAGFS client based on configuration.

    Args:
        agfs: RAGFS client instance.
        agfs_config: RAGFS configuration object containing backend settings.
    """
    # Check for the presence of a `mount` method
    if not callable(getattr(agfs, "mount", None)):
        return

    path_str = getattr(agfs_config, "path", None)
    if path_str is None:
        raise ValueError("agfs_config.path is required for mounting backend")

    data_path = Path(path_str).resolve()
    vikingfs_path = data_path / "viking"

    vikingfs_path.mkdir(parents=True, exist_ok=True)
    (data_path / "_system" / "queue").mkdir(parents=True, exist_ok=True)

    # 1. Mount standard plugins
    config = _generate_plugin_config(agfs_config, data_path)

    for plugin_name, plugin_config in config.items():
        mount_path = plugin_config["path"]
        # Ensure localfs directory exists before mounting
        if plugin_name == "localfs" and "local_dir" in plugin_config.get("config", {}):
            local_dir = plugin_config["config"]["local_dir"]
            os.makedirs(local_dir, exist_ok=True)
            logger.debug("[RAGFSUtils] Ensured localfs storage directory exists")
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
            logger.debug(f"[RAGFSUtils] Successfully mounted {plugin_name}")
        except Exception:
            logger.error(f"[RAGFSUtils] Failed to mount {plugin_name}")
