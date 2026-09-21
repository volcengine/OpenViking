# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent Evolution runtime configuration."""

from pydantic import BaseModel


class AgentEvolutionConfig(BaseModel):
    """Agent Evolution switch shared by cluster and account configuration."""

    enabled: bool = False
