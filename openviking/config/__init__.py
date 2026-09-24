# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime configuration system.

Separates configuration *storage/awareness* (:mod:`openviking.config.source`)
from configuration *merge/publish/invalidation* (:class:`RuntimeConfigManager`).

Cluster and account are two independent Pydantic models: cluster fields live on
``OpenVikingConfig`` and account fields on :class:`AccountConfig`. A field's
scope is decided by which model declares it. Business code should resolve
defaults from the independently published models. The manager's declarative
whole-section fallback remains only for compatibility with existing fields.
"""

from openviking.config.account_config import AccountConfig
from openviking.config.assembly import build_config_source, resolve_config_source_settings
from openviking.config.manager import (
    AccountCandidateContext,
    AccountCandidateValidator,
    ConfigChangeConsumer,
    ConfigChangeEvent,
    ConfigChangeReason,
    RuntimeConfigManager,
)
from openviking.config.merge import apply_three_state_patch, diff_sections
from openviking.config.scope import ConfigScope, ScopeKind
from openviking.config.source import (
    ConfigSource,
    ConfigSourceContext,
    FileConfigSource,
    MemoryConfigSource,
    Mutate,
    create_config_source,
    register_config_source,
)
from openviking.config.validate import ConfigPatchError, validate_patch
from openviking_cli.utils.config.open_viking_config import RuntimeConfigSettings

__all__ = [
    "AccountConfig",
    "AccountCandidateContext",
    "AccountCandidateValidator",
    "ConfigChangeConsumer",
    "ConfigChangeEvent",
    "ConfigChangeReason",
    "ConfigPatchError",
    "ConfigScope",
    "ConfigSource",
    "ConfigSourceContext",
    "FileConfigSource",
    "MemoryConfigSource",
    "Mutate",
    "RuntimeConfigManager",
    "RuntimeConfigSettings",
    "ScopeKind",
    "apply_three_state_patch",
    "build_config_source",
    "create_config_source",
    "diff_sections",
    "register_config_source",
    "resolve_config_source_settings",
    "validate_patch",
]
