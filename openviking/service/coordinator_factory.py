# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Factory entrypoint for coordination backend instances."""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING, Any

from .coordinator import InProcessCoordinator, RedisCoordinator

if TYPE_CHECKING:
    from openviking_cli.utils.config.storage_config import CoordinationConfig


def create_coordinator(config: "CoordinationConfig") -> Any:
    """Build a Coordinator from a CoordinationConfig.

    Built-in backends:
      - 'memory'  — in-process coordinator (default, single-machine)
      - 'redis'   — Redis-backed coordinator for multi-instance consistency.
                    DSN from config.redis.dsn or OPENVIKING_COORD_DSN env var.

    Custom backends: set config.backend to a full dotted class path,
    e.g. 'mycompany.module.CredisCoordinator'. The class must implement a
    from_config(cfg: CoordinationConfig) classmethod and return a
    Coordinator-compatible object. Extra parameters go in config.custom_params.
    """
    backend = config.backend

    if backend == "memory":
        return InProcessCoordinator()

    if backend == "redis":
        redis_cfg = config.redis
        dsn = redis_cfg.dsn or os.environ.get("OPENVIKING_COORD_DSN")
        if not dsn:
            raise ValueError(
                "storage.coordination.backend='redis' requires a DSN. "
                "Set storage.coordination.redis.dsn or the OPENVIKING_COORD_DSN environment variable."
            )
        return RedisCoordinator(
            dsn,
            key_prefix=redis_cfg.key_prefix,
            default_ttl_sec=redis_cfg.ttl_sec,
        )

    # Custom backend: full dotted class path e.g. 'mycompany.module.CredisCoordinator'
    if "." not in backend:
        raise ValueError(
            f"Unknown coordination backend: '{backend}'. "
            f"Built-in backends: 'memory', 'redis'. "
            f"For custom backends, use a full dotted class path "
            f"e.g. 'mycompany.module.ClassName'."
        )

    module_path, class_name = backend.rsplit(".", 1)
    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(f"coordination backend: cannot import module '{module_path}': {e}") from e
    try:
        cls: type = getattr(mod, class_name)
    except AttributeError:
        raise ValueError(
            f"coordination backend: class '{class_name}' not found in module '{module_path}'"
        )
    if not isinstance(cls, type):
        raise ValueError(f"coordination backend: '{class_name}' in '{module_path}' is not a class")
    if not hasattr(cls, "from_config"):
        raise ValueError(f"coordination backend: '{backend}' has no 'from_config' classmethod")
    result = cls.from_config(config)
    if result is None:
        raise ValueError(f"coordination backend: '{backend}.from_config()' returned None")
    return result
