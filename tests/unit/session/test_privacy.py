# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session privacy round-trips complete messages and rejects unsafe input."""

import json
import re
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message.part import ContextPart, ImagePart, TextPart, ToolPart
from openviking.privacy import session_extractor as extraction
from openviking.privacy.session_extractor import extract_session_privacy_values
from openviking.privacy.session_restore import get_session_id_from_uri, restore_session_content
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError


def message_jsonl(specs):
    return (
        "\n".join(
            json.dumps(
                {**spec, "parts": [asdict(part) for part in spec["parts"]]}, ensure_ascii=False
            )
            for spec in specs
        )
        + "\n"
    )


@pytest.fixture
def privacy():
    return SimpleNamespace(get_current=AsyncMock())


async def test_extract_and_restore_complete_messages(privacy, request_context):
    specs = [
        {
            "role": "user",
            "parts": [
                TextPart("password='中文密码'"),
                ContextPart(uri="viking://resource/doc", abstract="api_key=abstract-secret"),
                ImagePart(url="https://example.test/password=image-reference"),
            ],
        },
        {
            "role": "assistant",
            "parts": [
                ToolPart(
                    tool_input={
                        "credentials": {
                            "password": 'quote" and\nnewline',
                            "nested": [1, False, None],
                        },
                        "password": [],
                        "token": 1234,
                        "secret": {},
                        "nested": {"password": "another-secret"},
                        "region": "cn",
                    },
                    tool_output="password='hello world'",
                    tool_output_externalization_error="api_key=error-secret",
                ),
                ToolPart(tool_id="pending", tool_output=None),
            ],
        },
    ]
    before = deepcopy(specs)
    sanitized, values = extract_session_privacy_values("demo", specs)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{8}", key) for key in values)
    # Sensitive containers are captured whole, with no references inside them.
    assert list(values.values()) == [
        "abstract-secret",
        specs[1]["parts"][0].tool_input["credentials"],
        [],
        1234,
        {},
        "another-secret",
        "error-secret",
    ]
    assert specs == before
    clean = message_jsonl(sanitized)
    for secret in ["abstract-secret", "another-secret", "error-secret"]:
        assert secret not in clean
    privacy.get_current.return_value = SimpleNamespace(values=values)
    assert await restore_session_content(
        privacy, clean, request_context, session_id="demo", jsonl=True
    ) == message_jsonl(before)
    specs[1]["parts"][0].tool_input["credentials"]["nested"].append("changed")
    assert "changed" not in json.dumps(list(values.values()))


async def test_batch_field_id_collision_preserves_both_values(
    monkeypatch, privacy, request_context
):
    ids = iter(["same0001", "same0001", "next0002"])
    monkeypatch.setattr(extraction, "token_urlsafe", lambda _: next(ids))
    specs = [
        {"parts": [ToolPart(tool_input={"password": "first-secret", "api_key": "second-secret"})]}
    ]
    protected, values = extract_session_privacy_values("demo", specs)
    assert sorted(values.values()) == ["first-secret", "second-secret"]
    privacy.get_current.return_value = SimpleNamespace(values=values)
    assert await restore_session_content(
        privacy, message_jsonl(protected), request_context, session_id="demo", jsonl=True
    ) == message_jsonl(specs)


def test_exhausted_field_ids_reject_without_mutating_input(monkeypatch):
    monkeypatch.setattr(extraction, "token_urlsafe", lambda _: "same0001")
    specs = [
        {"parts": [ToolPart(tool_input={"password": "first-secret", "api_key": "second-secret"})]}
    ]
    before = deepcopy(specs)
    with pytest.raises(ResourceExhaustedError, match="unique Session privacy field IDs"):
        extract_session_privacy_values("demo", specs)
    assert specs == before


@pytest.mark.parametrize("key", ["password", "secret", "token", "api_key"])
@pytest.mark.parametrize(
    "value",
    [
        "dict",
        "только",
        "ordinary",
        "tool_input",
        "dictionary",
        "только так: слово должно остаться словом",
        "Keep this ordinary sentence in memory.",
        "这是一段普通说明文字，不是密钥。",
        '{"note":"keep these ordinary words"}',
        '{"password":"ordinary"}',
        "password=ordinary",
        "https://example.test/documentation",
    ],
)
def test_sensitive_field_names_preserve_words_and_prose(key, value):
    specs = [{"parts": [ToolPart(tool_input={key: value}), TextPart(value)]}]
    protected, values = extract_session_privacy_values("demo", specs)
    assert protected == specs
    assert values == {}


def test_sensitive_prose_field_extracts_only_embedded_credential():
    secret = "sk-0123456789abcdefghijklmnop"
    text = f"Keep these words; use {secret} for authentication."
    specs = [{"parts": [ToolPart(tool_input={"password": text})]}]
    protected, values = extract_session_privacy_values("demo", specs)
    [field] = values
    assert values[field] == secret
    assert protected[0]["parts"][0].tool_input["password"] == text.replace(
        secret, extraction.build_session_placeholder("demo", field)
    )


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_0123456789abcdefghijklmnop",
        "AKIAABCDEFGHIJKLMNOP",
        "Bearer abcdefghijklmnopqrstuv",
        "Basic dXNlcjpwYXNz",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature",
        "abcdef" * 6,
        "0123456789" * 4,
        "RaNdOmAlphabeticCredentialValue",
        "abcdefghijklmnopqrstuvwxyz" * 2,
        "https://user:password@example.test/path",
        "-----BEGIN PRIVATE KEY-----\n" + "YWJj" * 16 + "\n-----END PRIVATE KEY-----",
    ],
)
def test_sensitive_fields_still_capture_credential_shapes(secret):
    specs = [{"parts": [ToolPart(tool_input={"password": secret})]}]
    protected, values = extract_session_privacy_values("demo", specs)
    [field] = values
    assert values[field] == secret
    assert protected[0]["parts"][0].tool_input["password"] == (
        extraction.build_session_placeholder("demo", field)
    )


@pytest.mark.parametrize(
    "credential,secret",
    [
        ("api_key=sk-0123456789abcdefghijklmnop", "sk-0123456789abcdefghijklmnop"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("api_key=AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ],
)
def test_session_text_preserves_ordinary_assignments_and_extracts_real_credentials(
    credential, secret
):
    text = f'password="Keep these words in memory."\nsecret=ordinary\n{credential}'
    specs = [{"parts": [TextPart(text)]}]
    protected, values = extract_session_privacy_values("demo", specs)
    [field] = values
    assert values[field] == secret
    assert protected[0]["parts"][0].text == text.replace(
        secret, extraction.build_session_placeholder("demo", field)
    )


async def test_sensitive_field_names_do_not_leak_into_references(privacy, request_context):
    key = "sk-proj-12345678901234567890_api_key"
    specs = [{"parts": [ToolPart(tool_input={key: "secret-value"})]}]
    sanitized, values = extract_session_privacy_values("demo", specs)
    assert key not in message_jsonl(sanitized)
    privacy.get_current.return_value = SimpleNamespace(values=values)
    assert await restore_session_content(
        privacy, message_jsonl(sanitized), request_context, session_id="demo", jsonl=True
    ) == message_jsonl(specs)


@pytest.mark.parametrize("sensitive", [False, True], ids=["nested-input", "sensitive-container"])
@pytest.mark.parametrize(
    "limit,bound",
    [
        ("MAX_TEXT_CHARS", 20),
        ("MAX_BATCH_CHARS", 20),
        ("MAX_NODES", 4),
        ("MAX_MATCHES", 1),
        ("MAX_DEPTH", 1),
    ],
)
def test_limits_reject_batch_without_mutating_input(monkeypatch, limit, bound, sensitive):
    monkeypatch.setattr(extraction, limit, bound)
    value = {"a": {"password": "secret-value" * 4}}
    specs = [
        {
            "parts": [
                ToolPart(
                    tool_input={"credentials": value} if sensitive else value,
                    tool_output="password=another-secret",
                )
            ]
        }
    ]
    before = deepcopy(specs)
    with pytest.raises(InvalidArgumentError) as exc:
        extract_session_privacy_values("demo", specs)
    assert "secret-value" not in str(exc.value)
    assert specs == before


@pytest.mark.parametrize("kind", ["cycle", "non-string-key", "unsupported-value"])
def test_invalid_sensitive_container_is_rejected(kind):
    value = {"cycle": {}, "non-string-key": {1: "secret"}, "unsupported-value": {"x": object()}}[
        kind
    ]
    if kind == "cycle":
        value["self"] = value
    with pytest.raises(InvalidArgumentError):
        extract_session_privacy_values(
            "demo", [{"parts": [ToolPart(tool_input={"credentials": value})]}]
        )


@pytest.mark.parametrize(
    "owner,path,expected",
    [
        ("alice", "messages.jsonl", "demo"),
        ("alice", "history/archive_001/messages.jsonl", "demo"),
        ("alice", "tool-results/tool-1/output.txt", "demo"),
        ("alice", ".meta.json", None),
        ("alice", "history/archive_001/.overview.md", None),
        ("alice", "tool-results/tool-1/meta.json", None),
        ("bob", "messages.jsonl", None),
    ],
)
def test_restore_is_scoped_to_session_content(owner, path, expected):
    root = "viking://user/alice/sessions"
    assert get_session_id_from_uri(f"viking://user/{owner}/sessions/demo/{path}", root) == expected


@pytest.mark.parametrize("content", ["not json", '{"parts": null}'])
async def test_invalid_message_jsonl_is_rejected(privacy, request_context, content):
    with pytest.raises(InvalidArgumentError):
        await restore_session_content(
            privacy, content, request_context, session_id="demo", jsonl=True
        )
