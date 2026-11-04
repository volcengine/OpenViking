# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Business-level selection of Account-owned external parser credentials."""

from typing import Any

from openviking.config.account_config import AccountConfig
from openviking.config.manager import AccountConfigView
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig, ParserApiConfig


def resolve_parser_api_config(
    view: AccountConfigView[OpenVikingConfig, AccountConfig],
) -> ParserApiConfig:
    """Keep Cluster operational settings and replace only the Account identity.

    An absent Account section retains the legacy standalone Cluster credential.
    """
    cluster = view.cluster.parser_api
    account = view.account.parser_api
    if account is None:
        return cluster
    return cluster.model_copy(update={"api_key": account.api_key})


async def get_effective_parser_api_config(manager: Any, account_id: str) -> ParserApiConfig:
    """Read one current Account/Cluster publication pair for a parser operation."""
    return await manager.resolve_account(account_id, resolve_parser_api_config)
