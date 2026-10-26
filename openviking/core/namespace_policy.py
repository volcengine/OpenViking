# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing and capability policy for Viking URI namespaces."""

from dataclasses import dataclass
from enum import Enum

from openviking.core.namespace import uri_parts

SYSTEM_ROOT_URI = "viking://system"
SYSTEM_COMPILE_ROOT = f"{SYSTEM_ROOT_URI}/compile"
SYSTEM_EXAMPLE_SKILLS_ROOT = f"{SYSTEM_COMPILE_ROOT}/example-skills"
SYSTEM_MEMORY_ROOT = f"{SYSTEM_ROOT_URI}/memory"
SYSTEM_MEMORY_TEMPLATES_ROOT = f"{SYSTEM_MEMORY_ROOT}/memory_templates"
SYSTEM_MEMORY_BUILTIN_ROOT = f"{SYSTEM_MEMORY_TEMPLATES_ROOT}/builtin"
SYSTEM_MEMORY_CUSTOM_ROOT = f"{SYSTEM_MEMORY_TEMPLATES_ROOT}/custom"

SYSTEM_FIXED_DIRECTORIES = frozenset(
    {
        SYSTEM_ROOT_URI,
        SYSTEM_COMPILE_ROOT,
        SYSTEM_EXAMPLE_SKILLS_ROOT,
        SYSTEM_MEMORY_ROOT,
        SYSTEM_MEMORY_TEMPLATES_ROOT,
        SYSTEM_MEMORY_BUILTIN_ROOT,
        SYSTEM_MEMORY_CUSTOM_ROOT,
    }
)
SYSTEM_OFFICIAL_ASSET_ROOTS = frozenset(
    {
        SYSTEM_EXAMPLE_SKILLS_ROOT,
        SYSTEM_MEMORY_BUILTIN_ROOT,
    }
)


class NamespaceProcessingMode(str, Enum):
    INDEXED = "indexed"
    FILE_ONLY = "file_only"


@dataclass(frozen=True)
class NamespacePolicy:
    processing_mode: NamespaceProcessingMode
    supports_acl: bool
    supports_tags: bool
    supports_reindex: bool


_INDEXED_NAMESPACE_POLICY = NamespacePolicy(
    processing_mode=NamespaceProcessingMode.INDEXED,
    supports_acl=True,
    supports_tags=True,
    supports_reindex=True,
)
_SYSTEM_NAMESPACE_POLICY = NamespacePolicy(
    processing_mode=NamespaceProcessingMode.FILE_ONLY,
    supports_acl=False,
    supports_tags=False,
    supports_reindex=False,
)


def _is_same_or_descendant(uri: str, root_uri: str) -> bool:
    parts = uri_parts(uri)
    root_parts = uri_parts(root_uri)
    return parts[: len(root_parts)] == root_parts


def is_system_uri(uri: str) -> bool:
    """Return whether uri is the system namespace root or one of its descendants."""
    return _is_same_or_descendant(uri, SYSTEM_ROOT_URI)


def is_system_fixed_directory_uri(uri: str) -> bool:
    """Return whether uri is one of the fixed system directory paths."""
    normalized_parts = tuple(uri_parts(uri))
    return any(normalized_parts == tuple(uri_parts(root)) for root in SYSTEM_FIXED_DIRECTORIES)


def is_system_custom_asset_uri(uri: str) -> bool:
    """Return whether uri is an asset below the mutable custom template root."""
    parts = uri_parts(uri)
    root_parts = uri_parts(SYSTEM_MEMORY_CUSTOM_ROOT)
    return len(parts) > len(root_parts) and parts[: len(root_parts)] == root_parts


def namespace_policy_for_uri(uri: str) -> NamespacePolicy:
    """Return processing and capability policy for uri's namespace."""
    if is_system_uri(uri):
        return _SYSTEM_NAMESPACE_POLICY
    return _INDEXED_NAMESPACE_POLICY
