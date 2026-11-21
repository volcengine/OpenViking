# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Credential export boundary for explicit Admin user delegation."""

from openviking.server.identity import RequestContext
from openviking_cli.exceptions import PermissionDeniedError


def require_unbound_credential_export(ctx: RequestContext, operation: str) -> None:
    if ctx.delegated_by_user_id is not None:
        raise PermissionDeniedError(
            f"{operation} does not preserve Admin delegation; use a target-user credential"
        )
