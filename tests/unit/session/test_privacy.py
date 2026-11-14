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
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
    DEFAULT_SANITIZER_MAX_REPLACEMENTS,
)
from openviking.privacy.session_extractor import extract_session_privacy_values
from openviking.privacy.session_restore import get_session_id_from_uri, restore_session_content
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError

SECRET = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
SECOND_SECRET = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"


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


@pytest.fixture
def auto_privacy():
    return ResolvedPrivacyConfig(mode="auto")


@pytest.mark.parametrize("mode", ["off", "registered"])
async def test_non_auto_modes_skip_new_value_discovery(monkeypatch, request_context, mode):
    def unexpected_discovery(*args, **kwargs):
        pytest.fail("Session writes must not run new-value discovery")

    monkeypatch.setattr(extraction, "prepare_privacy_extractor", unexpected_discovery)
    privacy = SimpleNamespace(
        insert_values=AsyncMock(),
        get_settings=AsyncMock(return_value=ResolvedPrivacyConfig(mode=mode)),
    )
    specs = [
        {
            "role": "assistant",
            "parts": [
                TextPart("password=example-password sk-abcdefghijklmnopqrstuv"),
                ContextPart(uri="viking://resources/doc", abstract="api_key=example-key"),
                ToolPart(
                    tool_input={
                        "password": "example-password",
                        "token": 1234,
                        "secret": [],
                        "credentials": {"api_key": "example-key", "items": [False, None]},
                    },
                    tool_output="Authorization: Bearer example-token",
                    tool_output_externalization_error="password=example-error",
                ),
                ToolPart(tool_output=None),
                ImagePart(url="https://example.test/image"),
            ],
        }
    ]
    before = deepcopy(specs)
    protected = await extraction.extract_and_store_session_privacy(
        "discovery-disabled", specs, request_context, privacy, None
    )
    assert protected == before
    assert specs == before
    privacy.insert_values.assert_not_awaited()
    protected[0]["parts"][2].tool_input["credentials"]["items"].append("changed")
    protected[0]["parts"][0].text = "changed"
    assert specs == before


async def test_extract_and_restore_complete_messages(privacy, request_context, auto_privacy):
    specs = [
        {
            "role": "user",
            "parts": [
                TextPart(f"password='中文密码' API_KEY=`{SECRET}`"),
                ContextPart(uri="viking://resource/doc", abstract=f"api_key={SECOND_SECRET}"),
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
                            "api_key": SECRET,
                            "nested": [1, False, None],
                        },
                        "password": [],
                        "token": 1234,
                        "secret": {},
                        "nested": {"password": "another-secret"},
                        "region": "cn",
                    },
                    tool_output=f"password='hello world' {SECOND_SECRET}",
                    tool_output_externalization_error=f"api_key={SECRET}",
                ),
                ToolPart(tool_id="pending", tool_output=None),
            ],
        },
    ]
    before = deepcopy(specs)
    sanitized, values = extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{8}", key) for key in values)
    assert list(values.values()) == [SECRET, SECOND_SECRET]
    assert specs == before
    tool_input = sanitized[1]["parts"][0].tool_input
    assert tool_input["password"] == []
    assert tool_input["token"] == 1234
    assert tool_input["secret"] == {}
    assert tool_input["credentials"]["password"] == 'quote" and\nnewline'
    clean = message_jsonl(sanitized)
    assert SECRET not in clean and SECOND_SECRET not in clean
    privacy.get_current.return_value = SimpleNamespace(values=values)
    assert await restore_session_content(
        privacy, clean, request_context, session_id="demo", jsonl=True
    ) == message_jsonl(before)
    sanitized[1]["parts"][0].tool_input["credentials"]["nested"].append("changed")
    assert specs == before


async def test_batch_field_id_collision_preserves_both_values(
    monkeypatch, privacy, request_context, auto_privacy
):
    ids = iter(["same0001", "same0001", "next0002"])
    monkeypatch.setattr(extraction, "token_urlsafe", lambda _: next(ids))
    specs = [{"parts": [ToolPart(tool_input={"password": SECRET, "api_key": SECOND_SECRET})]}]
    protected, values = extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert list(values.values()) == [SECRET, SECOND_SECRET]
    privacy.get_current.return_value = SimpleNamespace(values=values)
    assert await restore_session_content(
        privacy, message_jsonl(protected), request_context, session_id="demo", jsonl=True
    ) == message_jsonl(specs)


def test_exhausted_field_ids_reject_without_mutating_input(monkeypatch, auto_privacy):
    monkeypatch.setattr(extraction, "token_urlsafe", lambda _: "same0001")
    specs = [{"parts": [ToolPart(tool_input={"password": SECRET, "api_key": SECOND_SECRET})]}]
    before = deepcopy(specs)
    with pytest.raises(ResourceExhaustedError, match="unique Session privacy field IDs"):
        extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert specs == before


async def test_credential_in_object_key_round_trips(privacy, request_context, auto_privacy):
    key = SECRET
    specs = [{"parts": [ToolPart(tool_input={key: "secret-value"})]}]
    sanitized, values = extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert key not in message_jsonl(sanitized)
    privacy.get_current.return_value = SimpleNamespace(values=values)
    assert await restore_session_content(
        privacy, message_jsonl(sanitized), request_context, session_id="demo", jsonl=True
    ) == message_jsonl(specs)


@pytest.mark.parametrize(
    "limit,bound",
    [
        ("DEFAULT_SANITIZER_MAX_CONTENT_BYTES", 20),
        ("MAX_NODES", 4),
        ("DEFAULT_SANITIZER_MAX_REPLACEMENTS", 1),
        ("MAX_DEPTH", 1),
    ],
)
def test_limits_reject_batch_without_mutating_input(monkeypatch, limit, bound, auto_privacy):
    monkeypatch.setattr(extraction, limit, bound)
    value = {"a": {"password": SECRET}}
    specs = [
        {
            "parts": [
                ToolPart(
                    tool_input=value,
                    tool_output=SECOND_SECRET,
                )
            ]
        }
    ]
    before = deepcopy(specs)
    with pytest.raises(InvalidArgumentError) as exc:
        extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert SECRET not in str(exc.value) and SECOND_SECRET not in str(exc.value)
    assert specs == before


def test_batch_byte_budget_accumulates_across_messages_and_parts(auto_privacy):
    half = DEFAULT_SANITIZER_MAX_CONTENT_BYTES // 2
    specs = [
        {"parts": [TextPart("🙂" * (half // 4))]},
        {"parts": [TextPart("x" * half)]},
    ]
    assert extract_session_privacy_values("demo", specs, settings=auto_privacy) == (specs, {})
    specs[1]["parts"].append(TextPart("x"))
    before = deepcopy(specs)
    with pytest.raises(InvalidArgumentError, match="text limit"):
        extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert specs == before


def test_batch_match_budget_counts_repeated_values_across_parts(auto_privacy):
    half = DEFAULT_SANITIZER_MAX_REPLACEMENTS // 2
    specs = [{"parts": [TextPart((SECRET + " ") * half), TextPart((SECRET + " ") * half)]}]
    protected, values = extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert list(values.values()) == [SECRET]
    reference = extraction.build_session_placeholder("demo", next(iter(values)))
    assert message_jsonl(protected).count(reference) == DEFAULT_SANITIZER_MAX_REPLACEMENTS
    specs[0]["parts"].append(TextPart(SECRET))
    before = deepcopy(specs)
    with pytest.raises(InvalidArgumentError, match="match limit"):
        extract_session_privacy_values("demo", specs, settings=auto_privacy)
    assert specs == before


@pytest.mark.parametrize("kind", ["cycle", "non-string-key", "unsupported-value"])
def test_invalid_payload_is_rejected(kind):
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
