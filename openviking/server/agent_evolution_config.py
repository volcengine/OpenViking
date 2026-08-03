# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Live access to the instance-wide Agent Evolution switch."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Optional

from openviking.server.config import ServerConfig
from openviking_cli.utils.config import load_json_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class AgentEvolutionConfigProvider:
    """Read the live Agent Evolution value from the configured ov.conf.

    Kubernetes projects Secret updates into the mounted config directory
    atomically. Reading the file at commit time lets already-created sessions
    observe the new value without restarting the server.
    """

    def __init__(
        self,
        *,
        default_enabled: bool,
        config_path: Optional[str | Path] = None,
    ) -> None:
        self._last_valid_enabled = bool(default_enabled)
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._runtime_override: Optional[tuple[bool, str]] = None
        self._lock = Lock()

    def set_default_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._last_valid_enabled = bool(enabled)

    def set_runtime_override(self, enabled: bool, revision: str) -> None:
        """Use a value immediately until the projected config catches up."""
        with self._lock:
            self._runtime_override = (bool(enabled), revision)
            self._last_valid_enabled = bool(enabled)

    def _fallback_enabled(self) -> bool:
        with self._lock:
            if self._runtime_override is not None:
                return self._runtime_override[0]
            return self._last_valid_enabled

    def is_enabled(self) -> bool:
        config_path = self._config_path
        if config_path is None:
            return self._fallback_enabled()

        try:
            payload = load_json_config(config_path)
            if not isinstance(payload, dict):
                raise ValueError("config root must be an object")
            server_config = payload.get("server", {})
            if server_config is None:
                server_config = {}
            if not isinstance(server_config, dict):
                raise ValueError("server must be an object")
            agent_evolution = ServerConfig.model_validate(server_config).agent_evolution
            enabled = agent_evolution.enabled
            revision = agent_evolution.revision
        except OSError as exc:
            logger.warning(
                "Failed to access Agent Evolution config file %s, using last valid value: %s",
                config_path,
                exc,
            )
            return self._fallback_enabled()
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to read Agent Evolution config from %s, using last valid value: %s",
                config_path,
                exc,
            )
            return self._fallback_enabled()

        with self._lock:
            if self._runtime_override is not None:
                override_enabled, override_revision = self._runtime_override
                if enabled == override_enabled and revision == override_revision:
                    self._runtime_override = None
                else:
                    return override_enabled
            self._last_valid_enabled = enabled
            return enabled
