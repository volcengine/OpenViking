# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression checks for public-deployment configuration guidance."""

import json
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_helm_chart_requires_operator_public_origin_configuration():
    """A public Kubernetes bind must not silently restore wildcard CORS."""
    values = yaml.safe_load(
        (REPOSITORY_ROOT / "deploy/helm/openviking/values.yaml").read_text()
    )

    server = values["config"]["server"]
    assert server["cors_origins"] == []
    assert server["public_base_url"] == ""
    assert server["webdav_max_body_bytes"] == 16 * 1024 * 1024


def test_cloud_examples_require_explicit_https_origins():
    """Copyable public examples must satisfy the application's fail-closed gate."""
    for relative_path in (
        "examples/cloud/ov.conf.example",
        "examples/multi_tenant/ov.conf.example",
    ):
        server = json.loads((REPOSITORY_ROOT / relative_path).read_text())["server"]
        assert server["cors_origins"] != ["*"]
        assert all(origin.startswith("https://") for origin in server["cors_origins"])
        assert server["public_base_url"].startswith("https://")
        assert server["webdav_max_body_bytes"] == 16 * 1024 * 1024
