# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Phase-1 data API permission checks."""

from openviking.server.identity import (
    DATA_READ_PERMISSION,
    DATA_WRITE_PERMISSION,
    DEFAULT_PERMISSION_PROFILE_ID,
    RequestContext,
)
from openviking_cli.exceptions import PermissionDeniedError


def require_permission(
    ctx: RequestContext,
    permission: str,
    *,
    operation: str,
    resource: str | None = None,
) -> None:
    """Raise a structured permission error when the context lacks the capability."""
    if ctx.has_permission(permission):
        return

    raise PermissionDeniedError(
        f"Permission denied for {operation}: requires {permission}.",
        resource=resource,
        details={
            "operation": operation,
            "required_permission": permission,
            "permission_profile": ctx.permission_profile or DEFAULT_PERMISSION_PROFILE_ID,
            "role": ctx.role.value,
            "effective_permissions": ctx.effective_permissions.to_dict(),
        },
    )


def require_data_read(
    ctx: RequestContext,
    *,
    operation: str,
    resource: str | None = None,
) -> None:
    """Require phase-1 read permission."""
    require_permission(ctx, DATA_READ_PERMISSION, operation=operation, resource=resource)


def require_data_write(
    ctx: RequestContext,
    *,
    operation: str,
    resource: str | None = None,
) -> None:
    """Require phase-1 write permission."""
    require_permission(ctx, DATA_WRITE_PERMISSION, operation=operation, resource=resource)
