# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for bridge configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from openviking_llamaparse_bridge.config import Settings, load_settings


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "LLAMA_CLOUD_API_KEY": "llx-test",
        "PARSER_BRIDGE_API_KEY": "bridge-secret-with-at-least-32-chars",
    }
    values.update(overrides)
    return values


def test_load_settings_uses_safe_defaults() -> None:
    settings = load_settings(_environment())

    assert settings.llama_base_url == "https://api.cloud.llamaindex.ai"
    assert settings.tier == "agentic"
    assert settings.version == "latest"
    assert settings.cost_optimizer is True
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 8080
    assert settings.artifact_cache_dir.name == "openviking-llamaparse-bridge"
    assert settings.artifact_cache_max_bytes == 1024 * 1024 * 1024
    assert settings.parse_options == {}


def test_load_settings_accepts_eu_and_advanced_options() -> None:
    settings = load_settings(
        _environment(
            LLAMAPARSE_REGION="EU",
            LLAMAPARSE_TIER="cost_effective",
            LLAMAPARSE_COST_OPTIMIZER="false",
            LLAMAPARSE_ORGANIZATION_ID="org-1",
            LLAMAPARSE_PROJECT_ID="project-1",
            BRIDGE_ARTIFACT_CACHE_DIR="/var/tmp/bridge-artifacts",
            BRIDGE_ARTIFACT_CACHE_MAX_BYTES="4096",
            LLAMAPARSE_PARSE_OPTIONS_JSON=(
                '{"input_options": {"spreadsheet": {"detect_sub_tables_in_sheets": true}}}'
            ),
        )
    )

    assert settings.llama_base_url == "https://api.cloud.eu.llamaindex.ai"
    assert settings.cost_optimizer is False
    assert settings.organization_id == "org-1"
    assert settings.project_id == "project-1"
    assert settings.artifact_cache_dir == Path("/var/tmp/bridge-artifacts")
    assert settings.artifact_cache_max_bytes == 4096
    assert settings.parse_options["input_options"]["spreadsheet"] == {
        "detect_sub_tables_in_sheets": True
    }


def test_load_settings_accepts_explicit_base_url_and_boolean_alias() -> None:
    settings = load_settings(
        _environment(
            LLAMAPARSE_REGION="unused",
            LLAMAPARSE_BASE_URL="https://llama.test/",
            LLAMAPARSE_COST_OPTIMIZER="yes",
        )
    )

    assert settings.llama_base_url == "https://llama.test"
    assert settings.cost_optimizer is True


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("llama_api_key", "LLAMA_CLOUD_API_KEY is required"),
        ("bridge_api_key", "PARSER_BRIDGE_API_KEY is required"),
    ],
)
def test_settings_rejects_blank_keys(field: str, message: str) -> None:
    values = {
        "llama_api_key": "llx-test",
        "bridge_api_key": "bridge-secret-with-at-least-32-chars",
    }
    values[field] = " "

    with pytest.raises(ValueError, match=message):
        Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"LLAMA_CLOUD_API_KEY": ""}, "LLAMA_CLOUD_API_KEY is required"),
        ({"PARSER_BRIDGE_API_KEY": "short"}, "at least 32 characters"),
        ({"LLAMAPARSE_REGION": "ap"}, "must be na or eu"),
        ({"LLAMAPARSE_TIER": "fast"}, "must be one of"),
        (
            {"LLAMAPARSE_TIER": "cost_effective"},
            "requires agentic or agentic_plus",
        ),
        ({"BRIDGE_PORT": "zero"}, "must be an integer"),
        ({"BRIDGE_PORT": "70000"}, "must be between 1 and 65535"),
        ({"BRIDGE_HTTP_TIMEOUT_SECONDS": "0"}, "must be greater than zero"),
        ({"BRIDGE_ARTIFACT_TTL_SECONDS": "0"}, "must be greater than zero"),
        ({"BRIDGE_ARTIFACT_CACHE_MAX_BYTES": "0"}, "must be greater than zero"),
        ({"BRIDGE_PUBLIC_URL": "localhost:8080"}, r"must be an HTTP\(S\) URL"),
        ({"BRIDGE_PUBLIC_URL": "http://localhost:8080?token=x"}, "query or fragment"),
        ({"LLAMAPARSE_PARSE_OPTIONS_JSON": "[]"}, "must contain a JSON object"),
        ({"LLAMAPARSE_PARSE_OPTIONS_JSON": "{bad"}, "must be valid JSON"),
        (
            {"LLAMAPARSE_PARSE_OPTIONS_JSON": '{"tier": "agentic_plus"}'},
            "cannot set bridge-managed fields: tier",
        ),
        (
            {"LLAMAPARSE_PARSE_OPTIONS_JSON": '{"output_options": []}'},
            "output_options must be an object",
        ),
        (
            {
                "LLAMAPARSE_PARSE_OPTIONS_JSON": (
                    '{"output_options": {"images_to_save": "embedded"}}'
                )
            },
            "images_to_save must be an array",
        ),
        (
            {"LLAMAPARSE_PARSE_OPTIONS_JSON": '{"output_options": null}'},
            "output_options must be an object",
        ),
        (
            {"LLAMAPARSE_PARSE_OPTIONS_JSON": '{"processing_options": null}'},
            "processing_options must be an object",
        ),
        (
            {"LLAMAPARSE_PARSE_OPTIONS_JSON": ('{"output_options": {"images_to_save": null}}')},
            "images_to_save must be an array",
        ),
    ],
)
def test_load_settings_rejects_invalid_values(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_settings(_environment(**overrides))
