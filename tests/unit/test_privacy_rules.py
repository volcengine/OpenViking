# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Credential rules distinguish secrets from business values and references."""

import pytest

from openviking.privacy.rules import find_privacy_matches

BODY = "abcd012345678901234567890123wxyz"


def matched_values(content):
    return [content[match.start : match.end] for match in find_privacy_matches(content)]


@pytest.mark.parametrize(
    "secret",
    [
        prefix + BODY
        for prefix in (
            "sk-",
            "sk-proj-",
            "sk-svcacct-",
            "sk-ant-api03-",
            "ak-",
            "ark-",
            "AKLT",
            "AKTP",
            "ghp_",
            "gho_",
            "ghu_",
            "ghs_",
            "ghr_",
            "github_pat_",
        )
    ]
    + ["AKIAABCDEFGHIJKLMNOP", "ASIAABCDEFGHIJKLMNOP"],
)
def test_known_credentials_in_free_text(secret):
    assert matched_values(f"凭据：{secret}；不要改动这些说明。") == [secret]


@pytest.mark.parametrize(
    "syntax",
    [
        'api_key="{value}"',
        '"clientSecret": "{value}"',
        "api key：'{value}'",
        'os.environ["AWS_SECRET_ACCESS_KEY"] = "{value}"',
        "VOLCENGINE_SECRET_ACCESS_KEY={value}",
        "MY-API-KEY={value}",
        "EVAL_ARK_API_KEY={value}",
        "DB_PASSWORD={value}",
        "AccessKeySecret={value}",
        "SLACK_BOT_TOKEN={value}",
        "privateKey={value}",
        "Authorization: Bearer {value}",
        "Proxy-Authorization: Basic {value}",
        "Authorization: basic {value}",
        '"Authorization": "Basic {value}"',
        "'Proxy-Authorization': 'Basic {value}'",
        "**Authorization: Basic {value}**",
    ],
)
def test_named_fields_capture_only_value(syntax):
    content = syntax.format(value=BODY)
    assert matched_values(content) == [BODY]


@pytest.mark.parametrize(
    "command,secret",
    [
        (f"run --api-key={BODY} --limit=10", BODY),
        (f"run --api-key {BODY} --limit 10", BODY),
        (f"run --access-key-secret '{BODY}'", BODY),
        (f"curl --oauth2-bearer {BODY} url", BODY),
        (f"curl -uuser:{BODY} url", f"user:{BODY}"),
        (f'curl -U "proxy:{BODY}" url', f"proxy:{BODY}"),
        ("curl --authorization 'Basic demo-secret'", "demo-secret"),
        ("run --password 'two words' --limit 10", "two words"),
        ('run --password "true" --limit 10', "true"),
        (r"run --password two\ words --limit 10", r"two\ words"),
        ('run --password pre"two words"post --limit 10', 'pre"two words"post'),
        ('run --password "escaped\\"value" --limit 10', 'escaped\\"value'),
        ("run --api-key \\\n    " + BODY, BODY),
    ],
)
def test_cli_credentials_without_expansion(command, secret):
    assert matched_values(command) == [secret]


def test_field_context_does_not_replace_same_value_in_prose():
    content = 'password="prod"\nThe prod configuration uses region=cn and max_tokens=8192.'
    [match] = find_privacy_matches(content)
    assert content[match.start : match.end] == "prod"
    assert content[match.end :] == '"\nThe prod configuration uses region=cn and max_tokens=8192.'


@pytest.mark.parametrize(
    "content",
    [
        "region=cn; max_tokens=8192; token_count=42; cache_key=cache-key",
        "base_url=https://example.test; project=prod; collection=default",
        "sk-example ark-example",
        'api_key="${API_KEY}"; password=$PASSWORD',
        "api_key=${API_KEY}; password=${DB_PASSWORD}",
        'curl --user alice --password --output result.json --user-agent "test client"',
        "{{ov_privacy:skill:sk-" + BODY + ":token}}",
    ],
)
def test_noncredentials_and_references_are_preserved(content):
    assert matched_values(content) == []


@pytest.mark.parametrize(
    "content,secret",
    [
        ("password=*", "*"),
        ('password="***"', "***"),
        ("run --password '[REDACTED]'", "[REDACTED]"),
        ("Authorization: Bearer ab****yz", "ab****yz"),
        ("Proxy-Authorization: Basic sk-ab****yz", "sk-ab****yz"),
        ("run --authorization 'Bearer ***'", "***"),
    ],
)
def test_mask_like_credentials_are_extracted(content, secret):
    assert matched_values(content) == [secret]


@pytest.mark.parametrize(
    "content",
    [
        "This is a basic example of configuration.",
        "Basic usage",
        "Use basic Python syntax.",
        "Basic dXNlcjpwYXNz",
    ],
)
def test_basic_without_authentication_context_is_not_a_credential(content):
    assert matched_values(content) == []
