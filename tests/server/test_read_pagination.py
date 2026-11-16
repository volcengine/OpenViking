# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure page-contract checks; these do not establish native/MCP integration."""

import importlib
import json

import pytest

import openviking.server.read_pagination as paging
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError

URI = "viking://resources/pagination/source.md"
SCOPE = ("default", "reader", "user")
KEY = b"synthetic-local-page-test-key-00001"


def call(text, *, budget=500, cursor=None, uri=URI, scope=SCOPE, key=KEY):
    raw = paging.build_read_page(text, uri, scope, budget, cursor, signing_key=key)
    assert isinstance(raw, str)
    assert estimate_text_tokens(raw) <= budget
    assert len(raw.encode("utf-8")) <= 1024 * 1024
    page = json.loads(raw)
    assert page["uri"] == uri
    assert type(page["has_more"]) is bool
    assert bool(page["next_cursor"]) == page["has_more"]
    return raw, page


@pytest.mark.parametrize("text", ["", "a", "line\r\nsecond\r\n", '中文🙂 "quoted" \\ path ' * 6000])
def test_lossless_json_budget(text):
    cursor = None
    chunks = []
    versions = set()
    for _ in range(len(text) + 1):
        raw, page = call(text, cursor=cursor)
        versions.add(page["version"])
        chunks.append(page["content"])
        if not page["has_more"]:
            break
        assert page["content"], "continuation must make progress"
        assert page["next_cursor"] != cursor
        assert raw == call(text, cursor=cursor)[0], "same position is deterministic"
        cursor = page["next_cursor"]
    else:
        pytest.fail("pagination did not terminate")
    assert "".join(chunks) == text
    assert len(versions) == 1


def test_fixed_key_survives_module_reload():
    text = "中文🙂line\r\n" * 1000
    first = call(text)[1]
    assert first["has_more"]
    expected = call(text, cursor=first["next_cursor"])[0]
    importlib.reload(paging)
    assert call(text, cursor=first["next_cursor"])[0] == expected


def test_changed_visible_text_requires_restart():
    text = "A\n" * 4000
    cursor = call(text)[1]["next_cursor"]
    with pytest.raises(FailedPreconditionError):
        call(text.replace("A", "B"), cursor=cursor)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"uri": URI + ".other"},
        {"scope": ("default", "other", "user")},
        {"key": b"different-synthetic-test-key-00000"},
    ],
)
def test_cursor_bound_to_request_contract(kwargs):
    text = "hello\r\n" * 1000
    cursor = call(text)[1]["next_cursor"]
    with pytest.raises(InvalidArgumentError):
        call(text, cursor=cursor, **kwargs)


@pytest.mark.parametrize("budget", [0, -1, 1, True])
def test_budget_cannot_return_empty_nonadvancing_page(budget):
    with pytest.raises(InvalidArgumentError):
        call("x" * 1000, budget=budget)


def test_budget_is_bound_to_cursor():
    text = "text\n" * 1000
    cursor = call(text)[1]["next_cursor"]
    with pytest.raises(InvalidArgumentError):
        call(text, budget=501, cursor=cursor)


@pytest.mark.parametrize("cursor", ["", "x", "!" * 98, "A" * 98, "é" * 98])
def test_invalid_cursor(cursor):
    with pytest.raises(InvalidArgumentError):
        call("text" * 1000, cursor=cursor)


def test_json_byte_ceiling():
    text = "🙂" * 300_000
    _, page = call(text, budget=10_000_000)
    assert page["has_more"]
    assert page["content"]


def test_shared_key_is_explicit_and_hidden():
    from pydantic import ValidationError

    from openviking.server.config import ServerConfig

    assert ServerConfig().read_cursor_signing_key is None
    with pytest.raises(ValidationError):
        ServerConfig(read_cursor_signing_key="too-short")
    config = ServerConfig(read_cursor_signing_key=KEY.decode())
    assert config.read_cursor_signing_key.get_secret_value().encode() == KEY
    assert KEY.decode() not in repr(config)
    assert KEY.decode() not in config.model_dump_json()
