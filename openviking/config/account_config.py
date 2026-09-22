# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped runtime configuration model.

``AccountConfig`` declares account overrides; ``OpenVikingConfig`` declares
cluster configuration. ``RuntimeField`` specifies write permissions and optional
cluster fallback. The manager resolves fallback for a whole unset section,
without merging cluster values into an explicitly configured account section.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from openviking_cli.utils.config.agent_evolution_config import AgentEvolutionConfig
from openviking_cli.utils.config.github_config import GitHubConfig
from openviking_cli.utils.config.runtime_field import RuntimeField


class AccountAclSettings(BaseModel):
    """Account-scoped ACL switch."""

    enabled: bool = RuntimeField(default=False)


class AccountFeishuConfig(BaseModel):
    """Sparse account-level Feishu overrides.

    ``domain`` intentionally does not exist here. It is a cluster deployment
    setting and is selected from the cluster configuration by the business
    resolver.
    """

    app_id: Optional[str] = RuntimeField(default=None)
    app_secret: Optional[str] = RuntimeField(default=None)
    max_rows_per_sheet: Optional[int] = RuntimeField(default=None, gt=0)
    max_records_per_table: Optional[int] = RuntimeField(default=None, gt=0)
    download_images: Optional[bool] = RuntimeField(default=None)
    request_timeout: Optional[float] = RuntimeField(default=None, gt=0)


class AccountConfig(BaseModel):
    """Sparse per-account override model.

    Every field is ``Optional`` and defaults to ``None`` (= "account did not
    override this"). ``fallback`` fields resolve to the named cluster field when
    unset.
    """

    # Account-level settings with active business consumers.
    feishu: Optional[AccountFeishuConfig] = RuntimeField(default=None)
    github: Optional[GitHubConfig] = RuntimeField(default=None)
    agent_evolution: Optional[AgentEvolutionConfig] = RuntimeField(
        default=None,
        fallback="agent_evolution",
    )
    acl: Optional[AccountAclSettings] = RuntimeField(default=None)

    # Deferred account-level sections (vlm, memory, embedding, vectordb)
    # have no active consumers. Ignore historical or newer persisted sections;
    # validate_patch() still keeps API writes limited to the fields above.
    model_config = {"arbitrary_types_allowed": True, "extra": "ignore"}
