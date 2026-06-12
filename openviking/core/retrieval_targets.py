# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Retrieval-only target resolution for search/find."""

from dataclasses import dataclass
from typing import List, Optional, Union

from openviking.core.namespace import (
    NamespaceShapeError,
    canonical_user_root,
    canonicalize_uri,
    uri_parts,
)
from openviking.core.peer_id import normalize_peer_id
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType
from openviking_cli.utils.uri import VikingURI


@dataclass(frozen=True)
class ResolvedRetrievalTargets:
    """Resolved retrieval target directories for find/search."""

    target_directories: List[str]
    first_explicit_directory: str = ""


def resolve_retrieval_targets(
    target_uri: Union[str, List[str]],
    ctx: RequestContext,
    peer_id: Optional[str],
) -> ResolvedRetrievalTargets:
    """Resolve search/find target directories."""
    normalized_peer_id = _normalize_peer_id(peer_id)
    target_uris = _canonicalize_target_uris(target_uri, ctx)

    if not target_uris:
        return ResolvedRetrievalTargets(
            target_directories=default_target_directories(ctx, peer_id=normalized_peer_id),
        )

    target_directories: List[str] = []
    for target in target_uris:
        for target_dir in _target_directories_for_uri(target, ctx=ctx, peer_id=normalized_peer_id):
            if target_dir not in target_directories:
                target_directories.append(target_dir)
    return ResolvedRetrievalTargets(
        target_directories=target_directories,
        first_explicit_directory=target_directories[0] if target_directories else "",
    )


def default_target_directories(
    ctx: Optional[RequestContext],
    *,
    peer_id: Optional[str] = None,
    context_type: Optional[ContextType] = None,
) -> List[str]:
    """Return default retrieval directories for a user context."""
    if not ctx or ctx.role == Role.ROOT:
        return []

    if context_type == ContextType.MEMORY:
        return _default_user_scoped_targets(ctx, peer_id, "memories")
    if context_type == ContextType.RESOURCE:
        return _default_resource_targets(ctx, peer_id)
    if context_type == ContextType.SKILL:
        return _default_skill_targets(ctx)
    return [
        *_default_user_scoped_targets(ctx, peer_id, "memories"),
        *_default_resource_targets(ctx, peer_id),
        *_default_skill_targets(ctx),
    ]


def _normalize_peer_id(peer_id: Optional[str]) -> Optional[str]:
    try:
        return normalize_peer_id(peer_id)
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def _canonicalize_target_uris(
    target_uri: Union[str, List[str]],
    ctx: RequestContext,
) -> List[str]:
    target_uri_list = [target_uri] if isinstance(target_uri, str) else (target_uri or [])
    target_uris: List[str] = []
    for item in target_uri_list:
        if not item or item in {"/", "viking://"}:
            continue
        try:
            target_uri = canonicalize_uri(item, ctx)
        except NamespaceShapeError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        if target_uri not in target_uris:
            target_uris.append(target_uri)
    return target_uris


def _target_directories_for_uri(
    target_uri: str,
    *,
    ctx: RequestContext,
    peer_id: Optional[str],
) -> List[str]:
    if _is_current_user_root(target_uri, ctx):
        return _default_user_root_targets(ctx, peer_id)

    peer_target = _resolve_peer_target(target_uri, ctx=ctx, peer_id=peer_id)
    if peer_target is not None:
        return peer_target

    for segment in ("memories", "resources", "skills"):
        if _is_default_user_content_root(target_uri, ctx, segment):
            if segment == "skills":
                return _default_skill_targets(ctx)
            return _default_user_scoped_targets(ctx, peer_id, segment)

    return [target_uri]


def _default_user_root_targets(ctx: RequestContext, peer_id: Optional[str]) -> List[str]:
    return [
        *_default_user_scoped_targets(ctx, peer_id, "memories"),
        *_default_user_scoped_targets(ctx, peer_id, "resources"),
        *_default_skill_targets(ctx),
    ]


def _default_resource_targets(ctx: RequestContext, peer_id: Optional[str]) -> List[str]:
    return [
        "viking://resources",
        *_default_user_scoped_targets(ctx, peer_id, "resources"),
    ]


def _default_skill_targets(ctx: RequestContext) -> List[str]:
    return [f"{canonical_user_root(ctx)}/skills"]


def _default_user_scoped_targets(
    ctx: RequestContext,
    peer_id: Optional[str],
    segment: str,
) -> List[str]:
    targets = [f"{canonical_user_root(ctx)}/{segment}"]
    if peer_id:
        targets.append(f"{canonical_user_root(ctx)}/peers/{peer_id}/{segment}")
    return targets


def _resolve_peer_target(
    target_uri: str,
    *,
    ctx: RequestContext,
    peer_id: Optional[str],
) -> Optional[List[str]]:
    parts = uri_parts(target_uri)
    user_root_parts = uri_parts(canonical_user_root(ctx))
    if parts[: len(user_root_parts)] != user_root_parts:
        return None

    suffix = parts[len(user_root_parts) :]
    if not suffix or suffix[0] != "peers":
        return None

    if len(suffix) == 1:
        raise InvalidArgumentError("target_uri must not point at all peer contexts.")

    target_peer_id = _normalize_peer_id(suffix[1])
    if peer_id and target_peer_id != peer_id:
        raise InvalidArgumentError("target_uri peer does not match peer_id.")

    peer_root = f"{canonical_user_root(ctx)}/peers/{target_peer_id}"
    if len(suffix) == 2:
        return [
            f"{peer_root}/memories",
            f"{peer_root}/resources",
        ]
    if suffix[2] not in {"memories", "resources"}:
        raise InvalidArgumentError("Only peer memories and resources are searchable.")
    return [target_uri]


def _is_current_user_root(target_uri: str, ctx: RequestContext) -> bool:
    normalized = VikingURI.normalize(target_uri).rstrip("/")
    return normalized in {"viking://user", canonical_user_root(ctx).rstrip("/")}


def _is_default_user_content_root(target_uri: str, ctx: RequestContext, segment: str) -> bool:
    normalized = VikingURI.normalize(target_uri).rstrip("/")
    return normalized in {
        f"viking://user/{segment}",
        f"{canonical_user_root(ctx).rstrip('/')}/{segment}",
    }
