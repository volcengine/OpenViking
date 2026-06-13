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
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
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
) -> ResolvedRetrievalTargets:
    """Resolve search/find target directories."""
    target_uris = _canonicalize_target_uris(target_uri, ctx)

    if not target_uris:
        return ResolvedRetrievalTargets(
            target_directories=default_target_directories(ctx),
        )

    target_directories: List[str] = []
    for target in target_uris:
        for target_dir in _target_directories_for_uri(target, ctx=ctx):
            if target_dir not in target_directories:
                target_directories.append(target_dir)
    return ResolvedRetrievalTargets(
        target_directories=target_directories,
        first_explicit_directory=target_directories[0] if target_directories else "",
    )


def default_target_directories(
    ctx: Optional[RequestContext],
    *,
    context_type: Optional[ContextType] = None,
) -> List[str]:
    """Return default retrieval directories for a user context."""
    if not ctx or ctx.role == Role.ROOT:
        return []

    if context_type == ContextType.MEMORY:
        return _default_user_scoped_targets(ctx, "memories")
    if context_type == ContextType.RESOURCE:
        return _default_resource_targets(ctx)
    if context_type == ContextType.SKILL:
        return _default_skill_targets(ctx)
    return _dedupe(
        [
            *_default_user_scoped_targets(ctx, "memories"),
            *_default_resource_targets(ctx),
            *_default_skill_targets(ctx),
        ]
    )


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
) -> List[str]:
    if _is_current_user_root(target_uri, ctx):
        return _default_user_root_targets(ctx)

    peer_target = _resolve_peer_target(target_uri, ctx=ctx)
    if peer_target is not None:
        return peer_target

    for segment in ("memories", "resources", "skills"):
        if _is_default_user_content_root(target_uri, ctx, segment):
            return [target_uri]

    return [target_uri]


def _default_user_root_targets(ctx: RequestContext) -> List[str]:
    return _dedupe(
        [
            *_default_user_scoped_targets(ctx, "memories"),
            *_default_user_scoped_targets(ctx, "resources"),
            *_default_skill_targets(ctx),
        ]
    )


def _default_resource_targets(ctx: RequestContext) -> List[str]:
    return [
        "viking://resources",
        *_default_user_scoped_targets(ctx, "resources"),
    ]


def _default_skill_targets(ctx: RequestContext) -> List[str]:
    if ctx.actor_peer_id:
        return []
    return [f"{canonical_user_root(ctx)}/skills"]


def _default_user_scoped_targets(
    ctx: RequestContext,
    segment: str,
) -> List[str]:
    user_root = canonical_user_root(ctx)
    if ctx.actor_peer_id:
        return [f"{user_root}/peers/{ctx.actor_peer_id}/{segment}"]
    if segment in {"memories", "resources"}:
        return [f"{user_root}/{segment}", f"{user_root}/peers"]
    return [f"{user_root}/{segment}"]


def _resolve_peer_target(
    target_uri: str,
    *,
    ctx: RequestContext,
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
    if ctx.actor_peer_id and target_peer_id != ctx.actor_peer_id:
        raise PermissionDeniedError("Actor peer cannot access another peer's context.")

    peer_root = f"{canonical_user_root(ctx)}/peers/{target_peer_id}"
    if len(suffix) == 2:
        return [
            f"{peer_root}/memories",
            f"{peer_root}/resources",
        ]
    if suffix[2] not in {"memories", "resources"}:
        raise InvalidArgumentError("Only peer memories and resources are searchable.")
    return [target_uri]


def _normalize_peer_id(peer_id: Optional[str]) -> Optional[str]:
    try:
        return normalize_peer_id(peer_id)
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def _dedupe(items: List[str]) -> List[str]:
    deduped: List[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _is_current_user_root(target_uri: str, ctx: RequestContext) -> bool:
    normalized = VikingURI.normalize(target_uri).rstrip("/")
    return normalized in {"viking://user", canonical_user_root(ctx).rstrip("/")}


def _is_default_user_content_root(target_uri: str, ctx: RequestContext, segment: str) -> bool:
    normalized = VikingURI.normalize(target_uri).rstrip("/")
    return normalized in {
        f"viking://user/{segment}",
        f"{canonical_user_root(ctx).rstrip('/')}/{segment}",
    }
