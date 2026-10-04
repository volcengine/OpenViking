# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for TTL policy configuration (``ttl_config``).

Assert the default is OFF, the ``inherit``/``disabled``/``days`` resolution
order, and the validators that keep the policy well-formed (positive ttl_days,
no ``inherit`` at the global level).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.ttl_config import (
    TTL_SCOPES,
    TTLCleanupConfig,
    TTLConfig,
    TTLPolicy,
)


def test_default_config_is_off():
    config = TTLConfig()
    assert config.enabled is False
    for scope in TTL_SCOPES:
        assert config.resolve_uri("", scope) is None


def test_scope_days_overrides_global():
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 7}},
        user_events={"mode": "days", "ttl_days": 30},
    )
    assert config.resolve_uri("", "user_events") == 30
    # sessions/peer_events inherit -> global default of 7
    assert config.resolve_uri("", "sessions") == 7
    assert config.resolve_uri("", "peer_events") == 7
    assert config.enabled is True


def test_cleanup_defaults_to_ready_with_day_level_physical_jitter():
    cleanup = TTLCleanupConfig()

    assert cleanup.enabled is True
    assert cleanup.cleanup_jitter_seconds == 24 * 60 * 60


def test_scope_disabled_blocks_global_inheritance():
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 7}},
        sessions={"mode": "disabled"},
    )
    assert config.resolve_uri("", "sessions") is None
    assert config.resolve_uri("", "user_events") == 7


def test_inherit_falls_through_to_global_off():
    # global disabled + all scopes inherit -> nothing enabled
    config = TTLConfig(**{"global": {"mode": "disabled"}})
    assert config.enabled is False
    assert config.resolve_uri("", "user_events") is None


def test_global_inherit_is_rejected():
    with pytest.raises(ValidationError):
        TTLConfig(**{"global": {"mode": "inherit"}})


def test_days_requires_positive_ttl_days():
    with pytest.raises(ValidationError):
        TTLPolicy(mode="days")  # missing ttl_days
    with pytest.raises(ValidationError):
        TTLPolicy(mode="days", ttl_days=0)  # ge=1
    with pytest.raises(ValidationError):
        TTLPolicy(mode="days", ttl_days=-5)


def test_ttl_days_must_be_omitted_unless_days_mode():
    with pytest.raises(ValidationError):
        TTLPolicy(mode="disabled", ttl_days=5)
    with pytest.raises(ValidationError):
        TTLPolicy(mode="inherit", ttl_days=5)


def test_global_alias_round_trips():
    # The field is named ``global_default`` but aliased to ``global`` for config.
    config = TTLConfig(**{"global": {"mode": "days", "ttl_days": 3}})
    assert config.global_default.ttl_days == 3
    dumped = config.model_dump(by_alias=True)
    assert dumped["global"]["ttl_days"] == 3


def test_directory_only_policy_enables_ttl_and_normalizes_slash():
    config = TTLConfig(directories={"viking://user/u1/sessions/": {"mode": "days", "ttl_days": 2}})
    assert config.enabled is True
    assert "viking://user/u1/sessions" in config.directories


def test_directory_key_must_be_concrete_user_uri():
    invalid_uris = [
        "/local/a",
        "viking://user/u1/preferences",
        "viking://user/u1/memories/entities",
        "viking://user/u1/sessions/s1",
        "viking://user/u1/memories/events/../entities",
        "viking://user/u1/memories/events//bad",
    ]
    for uri in invalid_uris:
        with pytest.raises(ValidationError):
            TTLConfig(directories={uri: {"mode": "disabled"}})


def test_resources_are_rejected():
    with pytest.raises(ValidationError):
        TTLConfig(resources={"mode": "days", "ttl_days": 7})
    with pytest.raises(ValidationError):
        TTLConfig(directories={"viking://resources/docs": {"mode": "days", "ttl_days": 7}})


@pytest.mark.parametrize("suffix", ["notes.md", "2026/09/28/event.md", "2026/02/30"])
def test_non_root_event_paths_reject_policy(suffix):
    with pytest.raises(ValidationError):
        TTLConfig(
            directories={
                "viking://user/u1/memories/events/" + suffix: {"mode": "days", "ttl_days": 7}
            }
        )


def test_session_defaults_reject_absolute_retention():
    for value in (
        {"sessions": {"mode": "absolute", "ttl_absolute": 2000000000}},
        {
            "directories": {
                "viking://user/u1/sessions": {"mode": "absolute", "ttl_absolute": 2000000000}
            }
        },
    ):
        with pytest.raises(ValueError, match="relative retention only"):
            TTLConfig.model_validate(value)


@pytest.mark.parametrize("suffix", ["/2026", "/2026/09", "/2026/09/30", "/2026/09/30/a.md"])
def test_only_events_root_accepts_policy(suffix):
    with pytest.raises(ValidationError):
        TTLConfig(
            directories={
                "viking://user/u1/memories/events" + suffix: {"mode": "days", "ttl_days": 7}
            }
        )


def test_root_policy_overrides_type_then_library_default():
    config = TTLConfig.model_validate(
        {
            "global": {"mode": "days", "ttl_days": 7},
            "user_events": {"mode": "days", "ttl_days": 30},
            "directories": {"viking://user/u1/memories/events": {"mode": "days", "ttl_days": 14}},
        }
    )
    assert (
        config.resolve_uri("viking://user/u1/memories/events/2026/09/30/a.md", "user_events") == 14
    )
    assert (
        config.resolve_uri("viking://user/u2/memories/events/2026/09/30/a.md", "user_events") == 30
    )
    assert config.resolve_uri("viking://user/u2/sessions/s1", "sessions") == 7
