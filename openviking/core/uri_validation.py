# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Validation helpers for externally supplied Viking URI fields."""

from collections.abc import Collection

from openviking.core.namespace import (
    NamespaceShapeError,
    classify_uri,
    is_accessible,
    resolve_request_uri,
    uri_parts,
)
from openviking_cli.exceptions import InvalidURIError, PermissionDeniedError
from openviking_cli.utils.uri import VikingURI

_PUBLIC_API_SCOPES = frozenset(
    {"", *VikingURI.PUBLIC_SCOPES, *VikingURI.LEGACY_SCOPES, *VikingURI.ALIAS_SCOPES}
)
_ALL_API_SCOPES = frozenset({"", *VikingURI.VISITABLE_SCOPES})


def _allowed_api_scopes(
    *,
    allow_internal: bool,
    allowed_scopes: Collection[str] | str | None,
) -> frozenset[str]:
    if allowed_scopes is not None:
        if isinstance(allowed_scopes, str):
            return frozenset({allowed_scopes})
        return frozenset(allowed_scopes)
    return _ALL_API_SCOPES if allow_internal else _PUBLIC_API_SCOPES


def validate_viking_uri(
    uri: str,
    *,
    field_name: str = "uri",
    allow_internal: bool = False,
    allowed_scopes: Collection[str] | str | None = None,
) -> str:
    """Validate a user-supplied URI in explicit ``viking://`` form."""
    raw_uri = uri.strip() if isinstance(uri, str) else ""
    if not raw_uri:
        raise InvalidURIError(str(uri), f"{field_name} must not be empty")

    try:
        parsed = VikingURI(raw_uri)
    except ValueError as exc:
        raise InvalidURIError(raw_uri, str(exc)) from exc

    scopes = _allowed_api_scopes(allow_internal=allow_internal, allowed_scopes=allowed_scopes)
    if parsed.scope not in scopes:
        # Alias scopes are accepted but never advertised in scope error messages.
        scope_names = ", ".join(
            sorted(scope for scope in scopes if scope and scope not in VikingURI.ALIAS_SCOPES)
        )
        if not parsed.scope:
            reason = f"URI must include one of: {scope_names}"
        else:
            reason = f"Invalid scope '{parsed.scope}'. Must be one of: {scope_names}"
        raise InvalidURIError(raw_uri, reason)

    return raw_uri


def validate_request_viking_uri(
    uri: str,
    ctx,
    *,
    field_name: str = "uri",
    allow_internal: bool = False,
    allowed_scopes: Collection[str] | str | None = None,
) -> str:
    """Validate an external URI and resolve supported aliases using request identity."""
    raw_uri = validate_viking_uri(
        uri,
        field_name=field_name,
        allow_internal=allow_internal,
        allowed_scopes=allowed_scopes,
    )
    try:
        return resolve_request_uri(raw_uri, ctx)
    except (ValueError, NamespaceShapeError) as exc:
        raise InvalidURIError(raw_uri, str(exc)) from exc


def validate_content_target_uri(
    uri: str,
    ctx,
    *,
    kind: str,
    field_name: str = "uri",
) -> str:
    """Validate and canonicalize add-resource/add-skill target URIs."""
    raw_uri = uri.strip() if isinstance(uri, str) else ""
    if not raw_uri:
        raise InvalidURIError(str(uri), f"{field_name} must not be empty")

    canonical_uri = validate_request_viking_uri(raw_uri, ctx, field_name=field_name)

    if _matches_content_kind(canonical_uri, kind):
        if is_accessible(canonical_uri, ctx):
            return canonical_uri
        raise PermissionDeniedError(f"Access denied for {canonical_uri}", resource=canonical_uri)

    raise InvalidURIError(raw_uri, f"{field_name} must target {kind} content")


def _matches_content_kind(uri: str, kind: str) -> bool:
    if kind not in {"resource", "skill"}:
        raise ValueError(f"Unsupported content target kind: {kind}")
    parts = uri_parts(uri)
    if kind == "resource" and parts[:1] == ["resources"]:
        return True
    classification = classify_uri(uri)
    return classification.context_type == kind and classification.content_index is not None
