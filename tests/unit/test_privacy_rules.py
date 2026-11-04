# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native credential spans preserve text and avoid incident false positives."""

import pytest

from openviking.privacy.rules import find_privacy_matches, is_sensitive_value

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
        "OPENAI_API_KEY=magpie\n# magpie\nyetone 的产品，官网 usemagpie.ai。",
        'password="deploy"; token=Optional[str]; secret=解码; api_key=token',
        r"API_KEY=$K\"; token=$TOKEN; password=$(grep TOKEN config); key=firstNotEmpty(92)",
        'api_key="${API_KEY}"; password=$PASSWORD; api_key=<your_api_key>',
        'password="***"; token=[REDACTED]; Authorization: Bearer ab****yz',
        "base_url=https://example.test; region=cn; max_tokens=8192; token_count=42",
        "sk-" + "abcdefghijklmnopqrstuv" + " ark-example ak-example",
    ],
)
def test_business_values_code_and_masked_examples_are_not_credentials(content):
    assert find_privacy_matches(content) == []
