# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.resource.github_radar.models import github_repository, validate_radar
from openviking_cli.exceptions import InvalidArgumentError
from tests.utils.radar_fixtures import REPO


@pytest.mark.parametrize(
    "path",
    [
        "https://github.com/volcengine/OpenViking.git",
        "git@github.com:volcengine/OpenViking",
        "ssh://git@github.com/volcengine/OpenViking",
    ],
)
def test_github_repository(path):
    assert github_repository(path) == REPO


@pytest.mark.parametrize(
    "path",
    ["https://gitlab.com/a/b", "https://github.com/a/b/pull/1", "https://github.com/../b"],
)
def test_github_repository_rejects_non_repository(path):
    with pytest.raises(InvalidArgumentError):
        github_repository(path)


@pytest.mark.parametrize(
    "raw",
    [
        {"enabled": True},
        {"enabled": True, "digest_interval_minutes": 0},
        {"enabled": True, "digest_interval_minutes": True},
        {"enabled": "true", "digest_interval_minutes": 30},
        {"enabled": True, "digest_interval_minutes": 30, "target_uri": "viking://resources/x"},
        {"enabled": False, "target_uri": "viking://user/alice/custom"},
        None,
    ],
)
def test_validate_radar_rejects_invalid_configuration(raw):
    with pytest.raises(InvalidArgumentError):
        validate_radar("https://github.com/a/b", {"pr_radar": raw}, watch_interval=1)


def test_validate_radar_requires_watch_and_keeps_interval_explicit():
    kwargs = {"pr_radar": {"enabled": True, "digest_interval_minutes": 90}}
    result = validate_radar("https://github.com/a/b", kwargs, watch_interval=1)
    assert result.digest_interval_minutes == 90
    with pytest.raises(InvalidArgumentError, match="native GitHub Watch"):
        validate_radar("https://github.com/a/b", kwargs, watch_interval=0)
    assert validate_radar("local", {}, watch_interval=0) is None
    assert not validate_radar("local", {"pr_radar": {}}, watch_interval=0).enabled
