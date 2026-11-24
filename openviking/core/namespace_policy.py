# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing and capability policy for Viking URI namespaces."""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import unquote

from openviking.core import namespace_constants as _namespace_constants
from openviking.core.namespace import uri_parts
from openviking_cli.exceptions import PermissionDeniedError

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext

MCP_SHARED_TOOLS_ROOT = "viking://agent/tools"
SYSTEM_ROOT_URI = _namespace_constants.SYSTEM_ROOT_URI
SYSTEM_COMPILE_ROOT = _namespace_constants.SYSTEM_COMPILE_ROOT
SYSTEM_COMPILE_GLOBAL_ROOT = _namespace_constants.SYSTEM_COMPILE_GLOBAL_ROOT
SYSTEM_EXAMPLE_SKILLS_ROOT = _namespace_constants.SYSTEM_EXAMPLE_SKILLS_ROOT
SYSTEM_MEMORY_ROOT = _namespace_constants.SYSTEM_MEMORY_ROOT
SYSTEM_MEMORY_TEMPLATES_ROOT = _namespace_constants.SYSTEM_MEMORY_TEMPLATES_ROOT
SYSTEM_MEMORY_BUILTIN_ROOT = _namespace_constants.SYSTEM_MEMORY_BUILTIN_ROOT
SYSTEM_MEMORY_CUSTOM_ROOT = _namespace_constants.SYSTEM_MEMORY_CUSTOM_ROOT
SYSTEM_FIXED_DIRECTORIES = _namespace_constants.SYSTEM_FIXED_DIRECTORIES
SYSTEM_OFFICIAL_ASSET_ROOTS = _namespace_constants.SYSTEM_OFFICIAL_ASSET_ROOTS


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


def _decoded_parts(uri: str) -> list[str]:
    """Decode per-segment so encoded catalog segments cannot evade the check."""
    return [unquote(part) for part in uri_parts(uri)]


def _mcp_catalog_parts(parts: list[str]) -> list[str]:
    if parts[:1] == ["user"] and len(parts) >= 2:
        return ["user", parts[1], "tools"]
    return uri_parts(MCP_SHARED_TOOLS_ROOT)


def _expand_home_alias(parts: list[str], ctx: "RequestContext | None") -> list[str]:
    if parts[:1] != ["~"]:
        return parts
    if ctx is None:
        return ["~", "tools"]
    return ["user", ctx.user.user_id, *parts[1:]]


def is_mcp_catalog_uri(uri: str, ctx: "RequestContext | None" = None) -> bool:
    parts = _expand_home_alias(_decoded_parts(uri), ctx)
    if parts[:2] == ["~", "tools"]:
        return True
    root = _mcp_catalog_parts(parts)
    return parts[: len(root)] == root


def ensure_mcp_catalog_access(
    uri: str, ctx: "RequestContext", *, include_descendants: bool = False
) -> None:
    """Keep ordinary file mutations and transfers out of managed MCP catalogs."""
    parts = _expand_home_alias(_decoded_parts(uri), ctx)
    root = _mcp_catalog_parts(parts)
    inside = parts[: len(root)] == root
    covers = include_descendants and (root[: len(parts)] == parts or parts == ["user"])
    catalog_root = "viking://" + "/".join(root)
    if inside and getattr(ctx, "_mcp_catalog_write_root", None) == catalog_root:
        return
    if inside or covers:
        raise PermissionDeniedError("Use MCP management to change tool catalogs", resource=uri)


def is_system_uri(uri: str) -> bool:
    """Return whether uri is the system namespace root or one of its descendants."""
    return _is_same_or_descendant(uri, SYSTEM_ROOT_URI)


def is_system_compile_global_uri(uri: str) -> bool:
    """Identify the externally managed Compile subtree without creating directories."""
    return _is_same_or_descendant(uri, SYSTEM_COMPILE_GLOBAL_ROOT)


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
