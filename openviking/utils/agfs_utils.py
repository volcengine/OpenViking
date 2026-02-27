# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
AGFS Client utilities for creating and configuring AGFS clients.
"""

import os
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
        # Setup library path if needed
        from pyagfs import AGFSBindingClient

        lib_path = getattr(agfs_config, "lib_path", None)
        if lib_path and lib_path not in ["1", "default"]:
            os.environ["AGFS_LIB_PATH"] = lib_path

        client = AGFSBindingClient()
        logger.info(f"[AGFSUtils] Created AGFSBindingClient (lib_path={lib_path})")

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

    # Only binding-client needs manual mounting, but we can also do it for HTTP client
    # if it supports it. Usually, HTTP server handles its own mounting.
    if not isinstance(agfs, AGFSBindingClient):
        return

    # 1. Mount standard plugins to align with HTTP server behavior
    # serverinfofs: /serverinfo
    try:
        agfs.unmount("/serverinfo")
    except Exception:
        pass
    try:
        agfs.mount("serverinfofs", "/serverinfo", {"version": "1.0.0"})
    except Exception as e:
        logger.warning(f"[AGFSUtils] Failed to mount serverinfofs at /serverinfo: {e}")

    # queuefs: /queue
    try:
        agfs.unmount("/queue")
    except Exception:
        pass
    try:
        agfs.mount("queuefs", "/queue", {})
    except Exception as e:
        logger.warning(f"[AGFSUtils] Failed to mount queuefs at /queue: {e}")

    # 2. Mount primary storage backend to /local
    backend = getattr(agfs_config, "backend", "local")
    mount_path = "/local"
    config = {}

    if backend == "local":
        path = getattr(agfs_config, "path", "./data")
        config = {"local_dir": str(path)}
    elif backend == "s3":
        s3_config = getattr(agfs_config, "s3", None)
        if s3_config:
            config = {
                "bucket": s3_config.bucket,
                "region": s3_config.region,
                "access_key_id": s3_config.access_key,
                "secret_access_key": s3_config.secret_key,
                "endpoint": s3_config.endpoint,
                "prefix": s3_config.prefix or "",
                "disable_ssl": not s3_config.use_ssl,
                "use_path_style": s3_config.use_path_style,
            }
    elif backend == "memory":
        # memfs plugin
        config = {}

    fstype = f"{backend}fs"
    if backend == "memory":
        fstype = "memfs"

    # Try to unmount existing mount at /local to allow backend switching
    try:
        agfs.unmount(mount_path)
    except Exception:
        pass

    try:
        agfs.mount(fstype, mount_path, config)
        logger.info(f"[AGFSUtils] Mounted {fstype} at {mount_path} with config={config}")
    except Exception as e:
        logger.error(f"[AGFSUtils] Failed to mount {fstype} at {mount_path}: {e}")
        raise e
