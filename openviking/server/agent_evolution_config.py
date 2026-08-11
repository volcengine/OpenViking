# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Live access to account-scoped Agent Evolution settings."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Optional

from openviking.server.account_settings import (
    effective_agent_evolution_enabled,
    read_account_settings,
)
from openviking.server.config import ServerConfig
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils.config import load_json_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class AgentEvolutionConfigProvider:
    """Resolve ov.conf defaults with persistent account overrides."""

    def __init__(
        self,
        *,
        default_enabled: bool,
        viking_fs: VikingFS,
        config_path: Optional[str | Path] = None,
    ) -> None:
        self._last_valid_enabled = bool(default_enabled)
        self._last_valid_account_overrides: dict[str, Optional[bool]] = {}
        self._viking_fs = viking_fs
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._lock = Lock()

    def set_default_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._last_valid_enabled = bool(enabled)

    def _default_enabled(self) -> bool:
        with self._lock:
            fallback = self._last_valid_enabled

        config_path = self._config_path
        if config_path is None:
            return fallback

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
        except OSError as exc:
            logger.warning(
                "Failed to access Agent Evolution config file %s, using last valid value: %s",
                config_path,
                exc,
            )
            return fallback
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to read Agent Evolution config from %s, using last valid value: %s",
                config_path,
                exc,
            )
            return fallback

        with self._lock:
            self._last_valid_enabled = enabled
            return enabled

    async def is_enabled(self, account_id: str) -> bool:
        default_enabled = self._default_enabled()
        try:
            settings = await read_account_settings(self._viking_fs, account_id)
            enabled = effective_agent_evolution_enabled(
                settings,
                default_enabled=default_enabled,
            )
            override = (
                settings.agent_evolution.enabled if settings.agent_evolution is not None else None
            )
            with self._lock:
                self._last_valid_account_overrides[account_id] = override
            return enabled
        except Exception as exc:
            logger.warning(
                "Failed to read Agent Evolution settings for account %s, using last valid value: %s",
                account_id,
                exc,
            )
            with self._lock:
                override = self._last_valid_account_overrides.get(account_id)
            return default_enabled if override is None else override
