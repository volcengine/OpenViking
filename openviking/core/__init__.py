# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Core context abstractions for OpenViking."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BuildingTree": ("openviking.core.building_tree", "BuildingTree"),
    "Context": ("openviking.core.context", "Context"),
    "ContextType": ("openviking.core.context", "ContextType"),
    "ResourceContentType": ("openviking.core.context", "ResourceContentType"),
    "SkillLoader": ("openviking.core.skill_loader", "SkillLoader"),
    "DirectoryDefinition": ("openviking.core.directories", "DirectoryDefinition"),
    "PRESET_DIRECTORIES": ("openviking.core.directories", "PRESET_DIRECTORIES"),
    "DirectoryInitializer": ("openviking.core.directories", "DirectoryInitializer"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(__all__))


__all__ = [
    # Context
    "Context",
    "ContextType",
    "ResourceContentType",
    # Tree
    "BuildingTree",
    # Skill
    "SkillLoader",
    # Directories
    "DirectoryDefinition",
    "PRESET_DIRECTORIES",
    "DirectoryInitializer",
]
