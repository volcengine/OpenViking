# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Mock context utilities for testing"""

from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def make_test_user(
    account_id: str = "acc1",
    user_id: str = "test_user",
    agent_id: str = "test_agent",
) -> UserIdentifier:
    """Create a test UserIdentifier"""
    return UserIdentifier(account_id, user_id, agent_id)


def make_test_ctx(
    user: UserIdentifier | None = None,
    role: Role = Role.ROOT,
    account_id: str = "acc1",
    user_id: str = "test_user",
    agent_id: str = "test_agent",
) -> RequestContext:
    """Create a test RequestContext"""
    if user is None:
        user = make_test_user(account_id, user_id, agent_id)
    return RequestContext(user=user, role=role)
