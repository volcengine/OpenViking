# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native credential detection preserves non-secret text and character spans."""

import pytest

from openviking.privacy import rules
from openviking.privacy.rules import find_privacy_matches, is_sensitive_value
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
    DEFAULT_SANITIZER_MAX_REPLACEMENTS,
)
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import InvalidArgumentError

SECRET = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"


def test_unicode_spans_preserve_markdown_and_existing_references():
    reference = "{{ ov_privacy:alice:viking://~/privacy/credential/" + SECRET + "#/values/key }}"
    content = f"标题🙂 `API_KEY={SECRET}`；{reference}\nAuthorization: Bearer {SECRET}"
    matches = find_privacy_matches(content)
    assert [content[m.start : m.end] for m in matches] == [SECRET, SECRET]
    assert {m.detector_id for m in matches} == {"volcengine_ak"}
    assert is_sensitive_value(SECRET)
    assert not is_sensitive_value(f"API_KEY={SECRET}")


@pytest.mark.parametrize(
    "content",
    [
        "OPENAI_API_KEY=sample\n# Example service\nDocs: https://sample.example.test",
        'password="staging"; token=Optional[str]; secret=示例文本; api_key=placeholder',
        r"API_KEY=$LOCAL_TOKEN\"; token=$TOKEN; password=$(cat settings.env); key=read_setting(17)",
        'api_key="${API_KEY}"; password=$PASSWORD; api_key=<your_api_key>',
        'password="***"; token=[REDACTED]; Authorization: Bearer ab****yz',
        "base_url=https://example.test; region=cn; max_tokens=8192; token_count=42",
        "sk-" + "abcdefghijklmnopqrstuv" + " ark-example ak-example",
    ],
)
def test_business_values_code_and_masked_examples_are_not_credentials(content):
    assert find_privacy_matches(content) == []


@pytest.mark.parametrize("unit", ["x", "中", "🙂"])
def test_extractor_accepts_sanitizer_byte_budget_and_rejects_overflow(unit):
    remaining = DEFAULT_SANITIZER_MAX_CONTENT_BYTES - len(SECRET) - 1
    unit_bytes = len(unit.encode("utf-8"))
    content = unit * (remaining // unit_bytes) + " " * (remaining % unit_bytes + 1) + SECRET
    matches = rules.prepare_privacy_extractor()(content)
    assert [content[m.start : m.end] for m in matches] == [SECRET]
    with pytest.raises(InvalidArgumentError, match="text limit"):
        find_privacy_matches(content + " ")


def test_extractor_accepts_sanitizer_match_budget_and_rejects_overflow():
    content = (SECRET + " ") * DEFAULT_SANITIZER_MAX_REPLACEMENTS
    assert len(find_privacy_matches(content)) == DEFAULT_SANITIZER_MAX_REPLACEMENTS
    with pytest.raises(InvalidArgumentError, match="match limit"):
        find_privacy_matches(content + SECRET)


def test_custom_detection_and_exact_exclusion_share_the_native_pipeline():
    value = "acme.gw_aB3dE5gH7jK9mN2pQ4sT"
    other = "ticket_xY7zA9bC2dE4"
    settings = ResolvedPrivacyConfig(
        custom_rules=[
            {"id": "gateway", "prefix": "acme.gw_", "min_length": 20},
            {"id": "ticket", "regex": "ticket_[A-Za-z0-9]{12}"},
        ],
        never_mask=[SECRET],
    )
    text = f"🙂 `{value}` {other} {SECRET} acmeXgw_aB3dE5gH7jK9mN2pQ4sT"
    query = rules.prepare_privacy_extractor(settings=settings)
    assert [text[m.start : m.end] for m in query(text)] == [
        value,
        other,
    ]
    assert is_sensitive_value(value, settings=settings)
    assert not is_sensitive_value(SECRET, settings=settings)
    assert not is_sensitive_value(value)  # Another user's built-in configuration.
