# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Independent Account settings with legacy Cluster defaults."""

from __future__ import annotations

from openviking.config.account_config import AccountConfig
from openviking.config.manager import RuntimeConfigManager
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


async def get_output_language_override(
    manager: RuntimeConfigManager[OpenVikingConfig, AccountConfig] | None,
    account_id: str,
    *,
    cluster_config: OpenVikingConfig | None = None,
) -> str:
    """Read the Account language; only an absent value uses Cluster."""
    if manager is not None:
        language = await manager.get_account(account_id, "output_language_override")
        if language is not None:
            return language
    config = cluster_config if cluster_config is not None else get_openviking_config()
    return config.output_language_override


async def get_memory_extraction_enabled(
    manager: RuntimeConfigManager[OpenVikingConfig, AccountConfig] | None,
    account_id: str,
    *,
    cluster_config: OpenVikingConfig | None = None,
) -> bool:
    """Read the Account extraction switch; only an absent section uses Cluster."""
    if manager is not None:
        memory = await manager.get_account(account_id, "memory")
        if memory is not None:
            return memory.extraction_enabled
    config = cluster_config if cluster_config is not None else get_openviking_config()
    return config.memory.extraction_enabled
