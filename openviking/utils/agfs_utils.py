# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
RAGFS Client utilities for creating and configuring RAGFS clients.
"""

import multiprocessing
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RagfsBindingConfig:
    """Single binding config object for both stack construction and backend mount setup."""

    agfs: Any
    root_key: bytes | None = None
    provider_type: int | None = None

    def encryption_enabled(self) -> bool:
        """Return whether the binding stack should include the encryption layer."""
        return self.root_key is not None

    def to_binding_dict(self) -> Dict[str, Any]:
        """Convert the runtime config into the sectioned dict consumed by `RAGFSBindingClient`."""
        binding_config: Dict[str, Any] = {}

        if self.root_key is not None:
            if len(self.root_key) != 32:
                raise ValueError("root_key must be exactly 32 bytes")
            if self.provider_type is None:
                raise ValueError("provider_type is required when root_key is configured")
            binding_config["encryption"] = {
                "root_key": self.root_key,
                "provider_type": self.provider_type,
            }

        return binding_config


def resolve_queuefs_mount_point(config: Any = None) -> str:
    """Resolve QueueFS mount point for the current process.

    `shared` keeps the historical global queue root (`/queue`).
    `worker` isolates each worker under `/queue/worker-<index|pid>`.
    """
    mode = None
    if config is not None:
        storage = getattr(config, "storage", None)
        if storage is None and hasattr(config, "agfs"):
            storage = config
        agfs = getattr(storage, "agfs", None) if storage is not None else None
        queuefs = getattr(agfs, "queuefs", None) if agfs is not None else None
        mode = getattr(queuefs, "mode", None)

    if not mode:
        try:
            from openviking_cli.utils.config import get_openviking_config

            mode = get_openviking_config().storage.agfs.queuefs.mode
        except Exception:
            mode = "shared"

    if mode == "worker":
        identity = getattr(multiprocessing.current_process(), "_identity", ())
        if identity:
            worker_id = str(identity[0] - 1)
        else:
            worker_id = str(os.getpid())
        return f"/queue/worker-{worker_id}"
    return "/queue"


def _build_queuefs_plugin_config(agfs_config: Any, data_path: Path) -> Dict[str, Any]:
    """Build QueueFS plugin configuration from AGFS config with legacy compatibility."""
    default_queue_db_path = data_path / "_system" / "queue" / "queue.db"
    queuefs_config = getattr(agfs_config, "queuefs", None)

    backend = getattr(queuefs_config, "backend", "sqlite") if queuefs_config else "sqlite"
    plugin_config: Dict[str, Any] = {
        "backend": backend,
        "recover_stale_sec": getattr(queuefs_config, "recover_stale_sec", 0),
        "busy_timeout_ms": getattr(queuefs_config, "busy_timeout_ms", 5000),
    }

    if backend in {"sqlite", "sqlite3"}:
        configured_queue_db_path = None
        if queuefs_config is not None:
            configured_queue_db_path = getattr(queuefs_config, "db_path", None)
        if not configured_queue_db_path:
            configured_queue_db_path = getattr(agfs_config, "queue_db_path", None)

        if configured_queue_db_path:
            queue_db_path = str(Path(configured_queue_db_path).expanduser().resolve())
        else:
            queue_db_path = str(default_queue_db_path)

        plugin_config["db_path"] = queue_db_path

    return plugin_config


def _generate_plugin_config(
    agfs_config: Any, data_path: Path, server_encryption_enabled: bool = False
) -> Dict[str, Any]:
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
            "config": _build_queuefs_plugin_config(agfs_config, data_path),
        },
    }

    backend = getattr(agfs_config, "backend", "local")
    s3_config = getattr(agfs_config, "s3", None)
    vikingfs_path = data_path / "viking"

    # Check for multi-write configuration
    backups_config = getattr(agfs_config, "backups", None)
    redirects_config = getattr(agfs_config, "redirects", None)

    # Build primary backend plugin config
    primary_plugin_config: Dict[str, Any] = {}

    if backend == "local":
        primary_plugin_config = {
            "local_dir": str(vikingfs_path),
        }
    elif backend == "s3" and s3_config:
        primary_plugin_config = _serialize_s3_plugin_params(s3_config)

    # Build the mount config dict for the primary backend
    mount_config: Dict[str, Any] = dict(primary_plugin_config)

    # Add multi-write fields if backups are configured
    if backups_config is not None:
        # Serialize backups config to dict for FFI JSON passthrough
        mount_config["backups"] = _serialize_backups_config(backups_config, data_path)
        mount_config["server_encryption_enabled"] = server_encryption_enabled
        mount_config["primary_encryption_enabled"] = server_encryption_enabled

        # Serialize redirect policies
        if redirects_config is not None:
            mount_config["primary_redirects"] = [
                _serialize_redirect_policy(p) for p in redirects_config
            ]

    # Determine the plugin type name for the primary backend
    if backend == "local":
        plugin_name = "localfs"
    elif backend == "s3":
        plugin_name = "s3fs"
    elif backend == "memory":
        plugin_name = "memfs"
    else:
        plugin_name = backend

    config[plugin_name] = {
        "enabled": True,
        "path": "/local",
        "config": mount_config,
    }

    return config


def _map_backend_to_plugin_name(backend: str) -> str:
    """Map user-facing backend name to Rust plugin name."""
    mapping = {
        "local": "localfs",
        "s3": "s3fs",
        "memory": "memfs",
    }
    return mapping.get(backend, backend)


def _serialize_s3_plugin_params(s3_config: Any) -> Dict[str, Any]:
    """Serialize user-facing S3 config into Rust s3fs plugin parameters."""
    directory_marker_mode = s3_config.directory_marker_mode
    return {
        "bucket": s3_config.bucket,
        "region": s3_config.region,
        "access_key_id": s3_config.access_key,
        "secret_access_key": s3_config.secret_key,
        "endpoint": s3_config.endpoint,
        "prefix": s3_config.prefix,
        "disable_ssl": not s3_config.use_ssl,
        "use_path_style": s3_config.use_path_style,
        "directory_marker_mode": directory_marker_mode.value
        if hasattr(directory_marker_mode, "value")
        else directory_marker_mode,
        "disable_batch_delete": s3_config.disable_batch_delete,
        "normalize_encoding_chars": s3_config.normalize_encoding_chars,
    }


def _dump_config_object(config: Any) -> Dict[str, Any]:
    """Dump a pydantic or namespace-like config object without None values."""
    if hasattr(config, "model_dump"):
        return config.model_dump(exclude_none=True)
    if isinstance(config, dict):
        return {key: value for key, value in config.items() if value is not None}
    return {
        key: value
        for key, value in vars(config).items()
        if value is not None and not key.startswith("_")
    }


def _serialize_backups_config(backups_config: Any, data_path: Path) -> Dict[str, Any]:
    """Serialize BackupsConfig to a dict suitable for JSON passthrough via FFI."""
    result: Dict[str, Any] = {
        "sync_type": getattr(backups_config, "sync_type", "async"),
    }
    if backups_config.write_ack_count is not None:
        result["write_ack_count"] = backups_config.write_ack_count
    if backups_config.write_ack_timeout_ms is not None:
        result["write_ack_timeout_ms"] = backups_config.write_ack_timeout_ms
    if backups_config.write_concurrency is not None:
        result["write_concurrency"] = backups_config.write_concurrency

    items = []
    for item in backups_config.items:
        item_dict: Dict[str, Any] = {
            "name": item.name,
            "backend": _map_backend_to_plugin_name(item.backend),
        }
        # Only include timeout if explicitly set (inherited default is 10)
        if "timeout" in item.model_fields_set:
            item_dict["timeout"] = item.timeout
        if item.encryption is not None:
            item_dict["encryption"] = {"enabled": item.encryption.enabled}
        if item.operations is not None:
            item_dict["operations"] = [
                {"operation": op.operation, "priority": op.priority} for op in item.operations
            ]
        if item.excludes is not None:
            item_dict["excludes"] = [_serialize_redirect_policy(p) for p in item.excludes]

        # Collect backend-specific params into "params" key for Rust BackendItemConfig
        # Check the original field names (e.g. "s3") not the mapped plugin names.
        backend_type = item.backend  # original user-facing name: "local", "s3", "memory"
        if backend_type == "s3" and "s3" in item.model_fields_set:
            s3_config = item.s3
            if s3_config is not None:
                item_dict["params"] = _serialize_s3_plugin_params(s3_config)
        elif backend_type == "local":
            local_config = getattr(item, "local", None)
            local_dir = None
            if isinstance(local_config, dict):
                local_dir = local_config.get("local_dir")
            elif local_config is not None:
                local_dir = getattr(local_config, "local_dir", None)
            if local_dir is None:
                local_dir = data_path / "viking" / "_backups" / item.name
            local_dir_path = Path(local_dir).expanduser()
            local_dir_path.mkdir(parents=True, exist_ok=True)
            item_dict["params"] = {"local_dir": str(local_dir_path)}
        elif backend_type in item.model_fields_set:
            backend_config = getattr(item, backend_type, None)
            if backend_config is not None:
                item_dict["params"] = _dump_config_object(backend_config)

        items.append(item_dict)

    result["items"] = items
    return result


def _serialize_redirect_policy(policy: Any) -> Dict[str, Any]:
    """Serialize a RedirectPolicyConfig to a dict."""
    result: Dict[str, Any] = {"type": policy.type}
    if policy.max_size_mb is not None:
        result["max_size_mb"] = policy.max_size_mb
    if policy.extensions is not None:
        result["extensions"] = policy.extensions
    if policy.target is not None:
        result["target"] = policy.target
    return result


def create_agfs_client(config: RagfsBindingConfig) -> Any:
    """
    Create a RAGFS client based on the provided configuration.

    Args:
        config: Single runtime config object containing both backend mount settings and
            construction-time binding sections.

    Returns:
        A RAGFSBindingClient instance.
    """
    if config is None:
        raise ValueError("config cannot be None")

    # Import binding client
    from openviking.pyagfs import get_binding_client

    RAGFSBindingClient, _ = get_binding_client()

    if RAGFSBindingClient is None:
        raise ImportError(
            "RAGFS binding client is not available. The native library (ragfs_python) "
            "could not be loaded. Please run 'pip install -e .' in the project root "
            "to build and install the RAGFS SDK with native bindings."
        )

    # Construction-time decides whether the stack includes the encryption layer.
    client = RAGFSBindingClient(config=config.to_binding_dict())

    # Automatically mount backend for binding client
    mount_agfs_backend(client, config)

    return client


def mount_agfs_backend(agfs: Any, config: RagfsBindingConfig | Any) -> None:
    """
    Mount backend filesystem for a RAGFS client based on configuration.

    Args:
        agfs: RAGFS client instance.
        config: RagfsBindingConfig or raw AGFS backend config for direct mount tests.
    """
    # Check for the presence of a `mount` method
    if not callable(getattr(agfs, "mount", None)):
        return

    agfs_config = config.agfs if isinstance(config, RagfsBindingConfig) else config
    path_str = getattr(agfs_config, "path", None)
    if path_str is None:
        raise ValueError("agfs_config.path is required for mounting backend")

    data_path = Path(path_str).resolve()
    vikingfs_path = data_path / "viking"

    vikingfs_path.mkdir(parents=True, exist_ok=True)

    # 1. Mount standard plugins
    server_encryption_enabled = (
        config.encryption_enabled() if isinstance(config, RagfsBindingConfig) else False
    )
    config = _generate_plugin_config(
        agfs_config, data_path, server_encryption_enabled=server_encryption_enabled
    )

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
            cfg = plugin_config.get("config", {})
            logger.debug(
                f"[RAGFSUtils] Mounting {plugin_name} at {mount_path} with config keys: {list(cfg.keys()) if isinstance(cfg, dict) else type(cfg)}"
            )
            agfs.mount(plugin_name, mount_path, cfg)
            logger.debug(f"[RAGFSUtils] Successfully mounted {plugin_name}")
        except Exception as e:
            logger.error(f"[RAGFSUtils] Failed to mount {plugin_name}: {e}")
            raise
