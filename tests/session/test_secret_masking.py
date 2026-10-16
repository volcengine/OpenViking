# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from copy import deepcopy

import pytest

from openviking.message import ContextPart, TextPart, ToolPart
from openviking.session import secret_masking as masking
from openviking.session.secret_masking import REDACTED, mask_message_specs, mask_text
from openviking_cli.exceptions import InvalidArgumentError

BODY = "abcd012345678901234567890123wxyz"


@pytest.mark.parametrize(
    "prefix",
    [
        "sk-",
        "sk-proj-",
        "sk-svcacct-",
        "sk-ant-api03-",
        "ak-",
        "ark-",
        "AKLT",
        "AKTP",
        "ghp_",
        "github_pat_",
    ],
)
def test_supported_credentials_are_completely_masked_and_idempotent(prefix):
    text = f"凭据 {prefix}{BODY}；再次使用 {prefix}{BODY}"
    expected = f"凭据 {prefix}ab****yz；再次使用 {prefix}ab****yz"
    assert mask_text(text) == expected
    assert mask_text(expected) == expected


@pytest.mark.parametrize("prefix", ["AKIA", "ASIA"])
def test_aws_key_ids_are_fully_masked(prefix):
    assert mask_text(prefix + "ABCDEFGHIJKLMNOP") == REDACTED


@pytest.mark.parametrize(
    "syntax",
    [
        "ak={value}",
        "sk={value}",
        'secret_key="{value}"',
        '"clientSecret": "{value}"',
        "api key：'{value}'",
        'os.environ["AWS_SECRET_ACCESS_KEY"] = "{value}"',
        "VOLCENGINE_SECRET_ACCESS_KEY={value}",
    ],
)
def test_sensitive_assignments_mask_unprefixed_credentials(syntax):
    assert mask_text(syntax.format(value=BODY)) == syntax.format(value="ab****yz")


@pytest.mark.parametrize(
    "name",
    [
        "API_KEY",
        "API-KEY",
        "MY-API-KEY",
        "MY_API-KEY",
        "apiKey",
        "EVAL_ARK_API_KEY",
        "MY_COMPANY_API_KEY",
        "_API_KEY",
    ],
)
def test_api_key_names_share_text_and_structured_field_normalization(name):
    assert mask_text(f'{name}="{BODY}"') == f'{name}="ab****yz"'
    result = mask_message_specs(
        [{"role": "assistant", "parts": [ToolPart(tool_input={name: BODY})]}]
    )
    assert result[0]["parts"][0].tool_input == {name: "ab****yz"}


@pytest.mark.parametrize(
    "name",
    [
        "DB_PASSWORD",
        "MY-REFRESH-TOKEN",
        "SERVICE-CLIENT_SECRET",
        "CUSTOM_SECRET_KEY",
        "AccessKeySecret",
        "MY_ACCESS_KEY_SECRET",
        "privateKey",
        "MY_SIGNING_SECRET",
        "webhookSecret",
        "security_token",
        "SLACK_BOT_TOKEN",
    ],
)
def test_qualified_fields_and_aliases_share_one_classifier(name):
    text = f'{name}="{BODY}"'
    assert mask_text(text) == f'{name}="ab****yz"'
    result = mask_message_specs([{"parts": [ToolPart(tool_input={name: BODY})]}])
    assert result[0]["parts"][0].tool_input == {name: "ab****yz"}


def test_credential_suffixes_keep_business_fields_and_full_boundaries():
    fields = {
        "DB_PASSWORD": BODY,
        "MY_ACCESS_TOKEN": BODY,
        "MY_COMPANY_API_KEY": BODY,
        "MY_COMPANY_API_KEY_ID": BODY,
        "MY_COMPANY_API_KEYS": BODY,
        "MY_COMPANY_NOTAPI_KEY": BODY,
        "MY_COMPANY_APIKEY": BODY,
        "MY-COMPANY-API-KEY-ID": BODY,
        "DB_PASSWORD_HINT": "use vault",
        "MY_ACCESS_TOKEN_COUNT": 42,
        "cache_key": BODY,
        "input_token": 1024,
        "token_count": 42,
        "max_tokens": 8192,
        "CUSTOM_NOTPASSWORD": BODY,
    }
    expected = {
        **fields,
        "DB_PASSWORD": "ab****yz",
        "MY_ACCESS_TOKEN": "ab****yz",
        "MY_COMPANY_API_KEY": "ab****yz",
    }
    text = "; ".join(f'{key}="{value}"' for key, value in fields.items())
    assert mask_text(text) == "; ".join(f'{key}="{value}"' for key, value in expected.items())
    result = mask_message_specs([{"parts": [ToolPart(tool_input=fields)]}])
    assert result[0]["parts"][0].tool_input == expected


@pytest.mark.parametrize(
    "command,expected",
    [
        ("run --api-key={value} --limit=10", "run --api-key=ab****yz --limit=10"),
        ("run --api-key {value} --limit 10", "run --api-key ab****yz --limit 10"),
        ('run --db-password="{value}"', 'run --db-password="ab****yz"'),
        ("run --access-key-secret '{value}'", "run --access-key-secret 'ab****yz'"),
        ("run --my-access-token {value}", "run --my-access-token ab****yz"),
        ("run --authorization 'Bearer {value}'", "run --authorization 'Bearer ab****yz'"),
        ("curl --oauth2-bearer {value} url", "curl --oauth2-bearer ab****yz url"),
        ("curl -u user:{value} url", "curl -u [REDACTED] url"),
        ("curl -uuser:{value} url", "curl -u[REDACTED] url"),
        ("curl --user='user:{value}' url", "curl --user='[REDACTED]' url"),
        ('curl -U "proxy:{value}" url', 'curl -U "[REDACTED]" url'),
        ("curl --proxy-user proxy:{value} url", "curl --proxy-user [REDACTED] url"),
        ("run --password 'two words' --limit 10", "run --password '[REDACTED]' --limit 10"),
        (r"run --password two\ words --limit 10", "run --password [REDACTED] --limit 10"),
        ('run --password pre"two words"post --limit 10', "run --password [REDACTED] --limit 10"),
        ('run --password "escaped\\"value" --limit 10', 'run --password "[REDACTED]" --limit 10'),
        ("run --api-key \\\n    {value} --limit 10", "run --api-key \\\n    ab****yz --limit 10"),
        ("run --password 'unfinished value", "run --password [REDACTED]"),
    ],
)
def test_command_line_values_are_completely_masked_and_idempotent(command, expected):
    result = mask_text(command.format(value=BODY))
    assert result == expected
    assert mask_text(result) == result


def test_command_line_scans_preserve_noncredential_arguments_and_count_matches(monkeypatch):
    command = 'curl --user alice --password --output result.json --user-agent "test client"'
    assert mask_text(command) == command
    monkeypatch.setattr(masking, "MAX_MATCHES", 0)
    with pytest.raises(InvalidArgumentError, match="match limit"):
        mask_text("run --password " + BODY)


@pytest.mark.parametrize("value", ["two words", 'escaped\\"value with spaces'])
def test_json_encoded_commands_mask_complete_quoted_values(value):
    import json

    record = {"command": f'run --password "{value}" --limit 10', "status": "retry"}
    expected = {"command": 'run --password "[REDACTED]" --limit 10', "status": "retry"}
    result = mask_text(json.dumps(record))
    assert json.loads(result) == expected
    assert mask_text(result) == result


def test_ark_tokens_consume_the_entire_body_and_preserve_surrounding_content():
    # Ark values can contain several hyphen-separated segments and non-hex chars.
    token = "ark-1256ca13-21f2-4124-8832-0411e3c355sb-e95ee"
    text = f"凭据：{token}；API_KEY='{token}'，max_tokens=8192 ark-example"
    expected = "凭据：ark-12****ee；API_KEY='ark-12****ee'，max_tokens=8192 ark-example"
    assert mask_text(text) == expected
    assert mask_text(expected) == expected
    assert mask_text("API_KEY=....") == "API_KEY=[REDACTED]"


@pytest.mark.parametrize(
    "value", ["short", "two words", 'abc\\"secret', "p@ssword", "ab****yzMORE"]
)
def test_short_complex_and_noncanonical_values_are_fully_masked(value):
    assert mask_text(f'password="{value}"') == 'password="[REDACTED]"'


@pytest.mark.parametrize("prefix", ["", "sk-proj-"])
@pytest.mark.parametrize("length", [19, 20])
def test_partial_mask_uses_twenty_character_body_threshold(prefix, length):
    body = "ab" + "0" * (length - 4) + "yz"
    expected = prefix + "ab****yz" if length == 20 else REDACTED
    assert mask_text(f'api_key="{prefix}{body}"') == f'api_key="{expected}"'


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("Authorization", "Bearer abcdefghijklmnopqrstuvwx", "Bearer ab****wx"),
        ("proxy_authorization", "Basic " + BODY, "Basic ab****yz"),
        ("authorization", "Bearer short", "Bearer [REDACTED]"),
        ("Authorization", "Bearer ab****yz", "Bearer ab****yz"),
    ],
)
def test_authentication_text_and_tool_input_preserve_scheme(name, value, expected):
    assert mask_text(f'{name}: "{value}"') == f'{name}: "{expected}"'
    result = mask_message_specs(
        [{"role": "assistant", "parts": [ToolPart(tool_input={name: value})]}]
    )
    assert result[0]["parts"][0].tool_input == {name: expected}


@pytest.mark.parametrize("delimiter", ["；", "，"])
def test_mixed_configuration_keeps_normal_content(delimiter):
    text = f"sk={BODY}{delimiter}max_tokens=8192 token_count=4 request_id={BODY} sk-example"
    expected = f"sk=ab****yz{delimiter}max_tokens=8192 token_count=4 request_id={BODY} sk-example"
    assert mask_text(text) == expected


def test_all_content_fields_are_masked_in_independent_copies():
    parts = [
        TextPart("sk-" + BODY),
        ContextPart(uri="viking://resources/doc", abstract="sk=" + BODY),
        ToolPart(
            tool_input={"nested": [{"secretAccessKey": BODY, "max_tokens": 42}]},
            tool_output="api_key=" + BODY,
            tool_output_externalization_error="token=" + BODY,
        ),
    ]
    original = deepcopy(parts)
    result = mask_message_specs([{"role": "assistant", "parts": parts}])[0]["parts"]
    assert parts == original
    assert result[0].text == "sk-ab****yz"
    assert result[1].abstract == "sk=ab****yz"
    assert result[2].tool_input == {"nested": [{"secretAccessKey": "ab****yz", "max_tokens": 42}]}
    assert result[2].tool_output == "api_key=ab****yz"
    assert result[2].tool_output_externalization_error == "token=ab****yz"
    result[2].tool_input["nested"][0]["max_tokens"] = 99
    assert parts[2].tool_input["nested"][0]["max_tokens"] == 42


def test_sensitive_values_are_masked_without_traversing_containers():
    cycle = {}
    cycle["loop"] = cycle
    deep = {"leaf": BODY}
    for _ in range(50):
        deep = {"nested": deep}
    original = {
        "credentials": cycle,
        "secret": deep,
        "token": None,
        "ak": 123,
        "password": "Bearer " + BODY,
    }
    result = mask_message_specs([{"role": "assistant", "parts": [ToolPart(tool_input=original)]}])
    assert result[0]["parts"][0].tool_input == dict.fromkeys(original, REDACTED)


@pytest.mark.parametrize(
    "limit", ["MAX_TEXT_CHARS", "MAX_BATCH_CHARS", "MAX_MATCHES", "MAX_NODES", "MAX_DEPTH"]
)
def test_resource_limits_reject_without_echoing_credentials(monkeypatch, limit):
    monkeypatch.setattr(masking, limit, 0 if limit == "MAX_MATCHES" else 1)
    specs = [
        {
            "role": "user",
            "parts": [
                ToolPart(tool_input={"nested": {"nested": {"sk": BODY}}}, tool_output="sk=" + BODY)
            ],
        }
    ]
    with pytest.raises(InvalidArgumentError) as error:
        mask_message_specs(specs)
    assert BODY not in str(error.value)


def test_sensitive_strings_still_count_toward_text_budget(monkeypatch):
    monkeypatch.setattr(masking, "MAX_TEXT_CHARS", len(BODY) - 1)
    with pytest.raises(InvalidArgumentError, match="text limit"):
        mask_message_specs([{"role": "assistant", "parts": [ToolPart(tool_input={"sk": BODY})]}])


def test_invalid_tool_input_and_masked_key_collisions_reject():
    cycle = {}
    cycle["loop"] = cycle
    collision = {"sk-ab" + "1" * 30 + "yz": 1, "sk-ab" + "2" * 30 + "yz": 2}
    for value in [cycle, {1: BODY}, {"custom": object()}, collision]:
        with pytest.raises(InvalidArgumentError):
            mask_message_specs([{"role": "assistant", "parts": [ToolPart(tool_input=value)]}])
