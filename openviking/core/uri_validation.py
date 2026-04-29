# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Validation helpers for externally supplied Viking URI fields."""

import re
from collections.abc import Collection

from openviking_cli.exceptions import InvalidURIError
from openviking_cli.utils.uri import VikingURI

_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_PUBLIC_API_SCOPES = frozenset({"", *VikingURI.PUBLIC_SCOPES})
_ALL_API_SCOPES = frozenset({"", *VikingURI.VISITABLE_SCOPES})


def _scope_from_uri(uri: str) -> str:
    normalized = uri if uri.startswith(f"{VikingURI.SCHEME}://") else VikingURI.normalize(uri)
    path = normalized[len(f"{VikingURI.SCHEME}://") :]
    if not path.strip("/"):
        return ""
    return path.split("/")[0]


def _format_scope_names(scopes: Collection[str]) -> str:
    return ", ".join(sorted(scope for scope in scopes if scope))


def _scope_set(
    *,
    allow_internal: bool,
    allowed_scopes: Collection[str] | str | None,
) -> frozenset[str]:
    if allowed_scopes is not None:
        if isinstance(allowed_scopes, str):
            return frozenset({allowed_scopes})
        return frozenset(allowed_scopes)
    return _ALL_API_SCOPES if allow_internal else _PUBLIC_API_SCOPES


def _invalid_scope_reason(scope: str, allowed_scopes: Collection[str]) -> str:
    scope_names = _format_scope_names(allowed_scopes)
    if not scope:
        return f"URI must include one of: {scope_names}"
    return f"Invalid scope '{scope}'. Must be one of: {scope_names}"


def validate_viking_uri(
    uri: str,
    *,
    field_name: str = "uri",
    allow_internal: bool = False,
    allowed_scopes: Collection[str] | str | None = None,
) -> str:
    """Validate a user-supplied Viking URI or supported short-format URI.

    Short formats such as ``resources/docs`` are allowed for existing CLI/API
    compatibility. Explicit non-viking schemes such as ``s3://`` or malformed
    viking schemes such as ``viking:/`` are rejected as INVALID_URI. Internal
    scopes such as ``temp`` and ``queue`` are rejected by default at the API
    boundary unless ``allow_internal`` or an explicit ``allowed_scopes`` is used.
    """
    raw_uri = uri.strip() if isinstance(uri, str) else ""
    if not raw_uri:
        raise InvalidURIError(str(uri), f"{field_name} must not be empty")

    scheme_match = _URI_SCHEME_RE.match(raw_uri)
    if scheme_match and not raw_uri.startswith(f"{VikingURI.SCHEME}://"):
        scheme = scheme_match.group(0)[:-1]
        if scheme == VikingURI.SCHEME:
            reason = f"URI must start with '{VikingURI.SCHEME}://'"
        else:
            reason = f"unsupported URI scheme '{scheme}'"
        raise InvalidURIError(raw_uri, reason)

    scopes = _scope_set(allow_internal=allow_internal, allowed_scopes=allowed_scopes)

    try:
        parsed = VikingURI(raw_uri)
    except ValueError as exc:
        reason = str(exc)
        if reason.startswith("Invalid scope"):
            reason = _invalid_scope_reason(_scope_from_uri(raw_uri), scopes)
        raise InvalidURIError(raw_uri, reason) from exc

    if parsed.scope not in scopes:
        raise InvalidURIError(raw_uri, _invalid_scope_reason(parsed.scope, scopes))

    return raw_uri


def validate_optional_viking_uri(
    uri: str | None,
    *,
    field_name: str = "uri",
    allow_internal: bool = False,
    allowed_scopes: Collection[str] | str | None = None,
) -> str:
    """Validate an optional Viking URI field, preserving empty-as-unspecified."""
    if uri is None:
        return ""
    raw_uri = uri.strip() if isinstance(uri, str) else ""
    if not raw_uri:
        return ""
    return validate_viking_uri(
        raw_uri,
        field_name=field_name,
        allow_internal=allow_internal,
        allowed_scopes=allowed_scopes,
    )


def validate_optional_viking_uris(
    uri: str | list[str] | None,
    *,
    field_name: str = "uri",
    allow_internal: bool = False,
    allowed_scopes: Collection[str] | str | None = None,
) -> str | list[str]:
    """Validate an optional Viking URI field that may be a single URI or a list.

    Like :func:`validate_optional_viking_uri` but also accepts ``list[str]``.
    Returns a validated ``str`` when the input is a single URI, or a
    ``list[str]`` with each element validated when the input is a list.
    Empty / ``None`` inputs produce ``""``; empty lists produce ``[]``.
    """
    if uri is None:
        return ""
    if isinstance(uri, list):
        validated: list[str] = []
        for item in uri:
            result = validate_optional_viking_uri(
                item,
                field_name=field_name,
                allow_internal=allow_internal,
                allowed_scopes=allowed_scopes,
            )
            if result:
                validated.append(result)
        return validated
    return validate_optional_viking_uri(
        uri,
        field_name=field_name,
        allow_internal=allow_internal,
        allowed_scopes=allowed_scopes,
    )
