# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Business-level resolution for account and cluster Feishu configuration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from openviking.config.account_config import AccountConfig
from openviking.config.manager import AccountConfigView
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.parser_config import FeishuConfig

_ACCOUNT_FEISHU_FIELDS = (
    "app_id",
    "app_secret",
    "max_rows_per_sheet",
    "max_records_per_table",
    "download_images",
    "request_timeout",
)


def resolve_feishu_config(
    view: AccountConfigView[OpenVikingConfig, AccountConfig],
) -> FeishuConfig:
    """Resolve one account's Feishu configuration.

    An account without a Feishu section uses the complete cluster configuration.
    Once the section exists, only the API domain remains cluster-owned; omitted
    account fields use ``FeishuConfig`` defaults instead of cluster values.
    """
    cluster_config = view.cluster.feishu
    account_config = view.account.feishu
    if account_config is None:
        return cluster_config

    account_defaults = FeishuConfig(domain=cluster_config.domain)
    updates = {
        field: value
        for field in _ACCOUNT_FEISHU_FIELDS
        if (value := getattr(account_config, field)) is not None
    }
    return replace(account_defaults, **updates)


async def get_effective_feishu_config(manager: Any, account_id: str) -> FeishuConfig:
    """Resolve Feishu config from one account/cluster publication pair."""
    return await manager.resolve_account(account_id, resolve_feishu_config)
