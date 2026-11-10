# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for model status observability."""

from openviking.storage.observers.models_observer import ModelsObserver


class _ConfiguredVLM:
    model = "astron-code-latest"
    provider = "litellm"

    def get_token_usage(self):
        return {"usage_by_model": {}}


class _ConfiguredVLMWithUnavailableUsage(_ConfiguredVLM):
    def get_token_usage(self):
        raise RuntimeError("usage backend unavailable")


def test_configured_vlm_is_visible_before_usage_is_recorded():
    status = ModelsObserver(vlm_instance=_ConfiguredVLM()).get_status_table()

    assert "VLM Models:" in status
    assert "astron-code-latest" in status
    assert "litellm" in status
    assert "configured" in status


def test_configured_vlm_is_visible_when_usage_lookup_fails():
    status = ModelsObserver(vlm_instance=_ConfiguredVLMWithUnavailableUsage()).get_status_table()

    assert "VLM Models:" in status
    assert "astron-code-latest" in status
