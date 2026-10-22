# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Authentication helpers for tests that do not exercise credential handling."""

from typing import Optional

from fastapi import Request

from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role


class FixedIdentityAuthPlugin(AuthPlugin):
    """Return one fixed identity without adding a production auth mode."""

    auth_mode = "test_fixed_identity"

    def __init__(
        self,
        *,
        role: Role = Role.ROOT,
        account_id: str = "default",
        user_id: str = "default",
    ) -> None:
        self._identity = ResolvedIdentity(
            role=role,
            account_id=account_id,
            user_id=user_id,
        )

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        del request, api_key, x_openviking_account, x_openviking_user
        return self._identity

    def validate_config(self, config) -> None:
        del config

    async def initialize(self, app, service, config) -> None:
        del app, service, config
