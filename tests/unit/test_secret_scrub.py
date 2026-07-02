# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the secret-scrub gate (issue #2899)."""

import os

import pytest

from openviking.privacy.secret_scrub import (
    is_secret_scrub_enabled,
    scrub_secrets,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Scrub state is env-driven; strip the knobs so tests start from a known base."""
    for k in ("OPENVIKING_SECRET_SCRUB", "OPENVIKING_SECRET_SCRUB_PATTERNS"):
        monkeypatch.delenv(k, raising=False)
    yield


def _enable(value="1"):
    os.environ["OPENVIKING_SECRET_SCRUB"] = value
    return value


def test_disabled_by_default_returns_text_unchanged():
    """Gate ships OFF — secret detection has FP risk, opt-in per the issue caveat."""
    text = "my key is sk-test-FAKE0123456789abcdef"
    out, count = scrub_secrets(text)
    assert out == text
    assert count == 0
    assert is_secret_scrub_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes"])
def test_enabled_truthy_values(monkeypatch, val):
    monkeypatch.setenv("OPENVIKING_SECRET_SCRUB", val)
    assert is_secret_scrub_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "", "no", "off"])
def test_enabled_falsy_values(monkeypatch, val):
    monkeypatch.setenv("OPENVIKING_SECRET_SCRUB", val)
    assert is_secret_scrub_enabled() is False


def test_openai_key_scrubbed_when_enabled():
    _enable()
    # Real OpenAI legacy key shape: sk- + 16+ alnum, no internal hyphens
    text = "config: sk-FAKE0123456789abcdefXYZ done"
    out, count = scrub_secrets(text)
    assert "sk-FAKE0123456789abcdefXYZ" not in out
    assert "REDACTED_SECRET" in out
    assert count == 1


def test_gemini_key_scrubbed():
    _enable()
    text = "gemini key AQabcdef1234567890_-XYZabcdefghij"
    out, count = scrub_secrets(text)
    assert "AQabcdef1234567890" not in out
    assert count == 1


def test_slack_token_scrubbed():
    _enable()
    text = "slack xoxb-1234567890-abcdef"
    out, count = scrub_secrets(text)
    assert "xoxb-" not in out
    assert count == 1


def test_bearer_header_scrubbed():
    _enable()
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
    out, count = scrub_secrets(text)
    assert "Bearer eyJhbGciOiJIUzI1NiJ9" not in out
    assert "REDACTED_SECRET" in out
    assert count == 1


def test_github_token_scrubbed():
    _enable()
    # gh[pousr]_ + 36+ alnum
    text = "GH token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    out, count = scrub_secrets(text)
    assert "ghp_" not in out
    assert count == 1


def test_multiple_secrets_scrubbed():
    _enable()
    text = "keys: sk-AAAAAAAAAAAAAAAA and xoxp-9988776655-44332211"
    out, count = scrub_secrets(text)
    assert "sk-AAAA" not in out
    assert "xoxp-" not in out
    assert count == 2


def test_legitimate_identifiers_not_scrubbed():
    """FP guard: commit SHAs, UUIDs, short hex must survive when the gate is on."""
    _enable()
    # 40-char hex commit SHA — NOT a named secret shape, must survive
    sha = "a" * 40
    uuid4 = "550e8400-e29b-41d4-a716-446655440000"
    text = f"commit {sha} session {uuid4}"
    out, count = scrub_secrets(text)
    assert out == text
    assert count == 0


def test_idempotent():
    """Re-scrubbing scrubbed text is a no-op (REDACTED marker has no secret shape)."""
    _enable()
    text = "key sk-test-FAKE0123456789abcdef here"
    once, _ = scrub_secrets(text)
    twice, count = scrub_secrets(once)
    assert twice == once
    assert count == 0


def test_empty_text_passthrough():
    _enable()
    assert scrub_secrets("") == ("", 0)


def test_custom_patterns_via_env(monkeypatch):
    """Operator can extend for internal key shapes without code changes."""
    monkeypatch.setenv("OPENVIKING_SECRET_SCRUB", "1")
    monkeypatch.setenv("OPENVIKING_SECRET_SCRUB_PATTERNS", "INTERNAL-KEY-[0-9]+")
    out, count = scrub_secrets("token INTERNAL-KEY-99999 end")
    assert "INTERNAL-KEY-99999" not in out
    assert "REDACTED_SECRET" in out
    assert count == 1


def test_custom_patterns_override_replaces_defaults(monkeypatch):
    """When env patterns are set, default patterns no longer apply (override semantics)."""
    monkeypatch.setenv("OPENVIKING_SECRET_SCRUB", "1")
    monkeypatch.setenv("OPENVIKING_SECRET_SCRUB_PATTERNS", "INTERNAL-KEY-[0-9]+")
    out, count = scrub_secrets("sk-test-FAKE0123456789abcdef")
    # default OpenAI pattern NOT in effect when overridden
    assert out == "sk-test-FAKE0123456789abcdef"
    assert count == 0
