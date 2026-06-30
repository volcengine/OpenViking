# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""IngestConfig gating + value validation."""

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.ingest_config import (
    CommitPolicy,
    IngestConfig,
    IngestHarnessConfig,
)


def test_master_switch_gates_all_harnesses():
    enabled_h = {"claude_code": IngestHarnessConfig(enabled=True, mode="both")}
    assert IngestConfig(enabled=False, harnesses=enabled_h).enabled_harnesses() == {}
    assert list(IngestConfig(enabled=True, harnesses=enabled_h).enabled_harnesses()) == [
        "claude_code"
    ]


def test_off_mode_excluded():
    cfg = IngestConfig(
        enabled=True,
        harnesses={
            "claude_code": IngestHarnessConfig(enabled=True, mode="off"),
            "codex": IngestHarnessConfig(enabled=False, mode="both"),
        },
    )
    assert cfg.enabled_harnesses() == {}


def test_positive_value_validation():
    with pytest.raises(ValidationError):
        IngestHarnessConfig(poll_interval_seconds=0)
    with pytest.raises(ValidationError):
        CommitPolicy(commit_token_threshold=0)
    with pytest.raises(ValidationError):
        CommitPolicy(commit_idle_seconds=-1)
