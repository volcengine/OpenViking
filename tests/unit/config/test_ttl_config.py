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
        assert config.resolve_scope(scope) is None


def test_scope_days_overrides_global():
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 7}},
        user_events={"mode": "days", "ttl_days": 30},
    )
    assert config.resolve_scope("user_events") == 30
    # sessions/peer_events inherit -> global default of 7
    assert config.resolve_scope("sessions") == 7
    assert config.resolve_scope("peer_events") == 7
    assert config.enabled is True


def test_library_global_never_applies_to_resources_but_resource_directory_can_opt_in():
    root = "viking://user/u1/resources/project"
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 30}},
        directories={root: {"mode": "days", "ttl_days": 7}},
    )

    assert config.resolve_scope("resources") is None
    assert config.resolve_uri("viking://resources/public/doc.md", "resources") is None
    assert config.resolve_uri("viking://user/u1/resources/private.md", "resources") is None
    assert config.resolve_uri(root + "/doc.md", "resources") == 7


def test_cleanup_defaults_to_ready_with_day_level_physical_jitter():
    cleanup = TTLCleanupConfig()

    assert cleanup.enabled is True
    assert cleanup.cleanup_jitter_seconds == 24 * 60 * 60


def test_scope_disabled_blocks_global_inheritance():
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 7}},
        sessions={"mode": "disabled"},
    )
    assert config.resolve_scope("sessions") is None
    assert config.resolve_scope("user_events") == 7


def test_inherit_falls_through_to_global_off():
    # global disabled + all scopes inherit -> nothing enabled
    config = TTLConfig(**{"global": {"mode": "disabled"}})
    assert config.enabled is False
    assert config.resolve_scope("user_events") is None


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


def test_nearest_directory_override_inherits_explicit_parent():
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 7}},
        user_events={"mode": "days", "ttl_days": 30},
        directories={
            "viking://user/u1/memories/events": {"mode": "days", "ttl_days": 14},
            "viking://user/u1/memories/events/private/": {"mode": "disabled"},
            "viking://user/u1/memories/events/private/shared": {"mode": "inherit"},
        },
    )
    assert config.resolve_uri("viking://user/u1/memories/events/e.md", "user_events") == 14
    assert (
        config.resolve_uri("viking://user/u1/memories/events/private/e.md", "user_events") is None
    )
    assert (
        config.resolve_uri("viking://user/u1/memories/events/private/shared/e.md", "user_events")
        is None
    )


def test_directory_matching_respects_path_boundaries():
    config = TTLConfig(
        directories={"viking://user/u1/memories/events/a": {"mode": "days", "ttl_days": 9}}
    )
    assert config.resolve_uri("viking://user/u1/memories/events/abc/e.md", "user_events") is None


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


def test_directory_names_do_not_determine_object_type():
    directory = "viking://user/u1/memories/events/notes.md"
    config = TTLConfig(directories={directory: {"mode": "days", "ttl_days": 3}})
    assert config.resolve_uri(directory + "/child.txt", "user_events") == 3
    assert config.resolve_uri(directory, "user_events") is None
