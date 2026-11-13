# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native credential detection preserves non-secret text and character spans."""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, call

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


@pytest.fixture
def empty_extractor_cache(monkeypatch):
    monkeypatch.setattr(rules, "_extractor_cache", {})
    monkeypatch.setattr(rules, "_extractor_cache_bytes", 0)
    monkeypatch.setattr(
        rules, "_extractor_cache_max_bytes", rules.DEFAULT_EXTRACTOR_CACHE_MAX_BYTES
    )
    monkeypatch.setattr(rules, "_extractor_limits", {})


@pytest.mark.usefixtures("empty_extractor_cache")
def test_extractor_cache_shares_rules_and_expires_after_ttl(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(rules, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(rules, "DEFAULT_MATCHER_CACHE_TTL_SECONDS", 60.0)

    def compile_rules(_):
        engine = SimpleNamespace(extract=Mock(return_value=[]), reserved_bytes=0)
        engine.clone_for_search = lambda: engine
        return engine

    constructor = Mock(side_effect=compile_rules)
    monkeypatch.setattr(rules, "get_privacy_extractor_class", lambda: constructor)
    find_privacy_matches("public", settings=ResolvedPrivacyConfig(never_mask=["one"]))
    find_privacy_matches("public", settings=ResolvedPrivacyConfig(never_mask=["two"]))
    constructor.assert_called_once_with("[]")
    shared = rules._extractor()
    shared.extract.assert_has_calls([call("public", ["one"]), call("public", ["two"])])
    definitions = [
        json.dumps([{"id": f"rule{i}", "prefix": f"token{i}_", "min_length": 12}], sort_keys=True)
        for i in range(2)
    ]
    clock[0] = 1
    engines = {key: rules._extractor(key) for key in definitions}
    clock[0] = 59
    for key, engine in engines.items():
        assert rules._extractor(key) is engine
    assert rules._extractor() is shared
    assert constructor.call_count == 3
    clock[0] = 60
    assert rules._extractor() is not shared
    assert rules._extractor(definitions[0]) is engines[definitions[0]]
    clock[0] = 61
    assert rules._extractor(definitions[0]) is not engines[definitions[0]]
    assert len(rules._extractor_cache) == 2
    assert constructor.call_count == 5


@pytest.mark.usefixtures("empty_extractor_cache")
def test_extractor_cache_charges_native_reservations(monkeypatch):
    reserved = 10 * 1024 * 1024
    constructor = Mock(
        side_effect=lambda value: SimpleNamespace(key=value, reserved_bytes=reserved)
    )
    monkeypatch.setattr(rules, "get_privacy_extractor_class", lambda: constructor)
    definitions = [f"rules-{index}" for index in range(7)]

    for definition in definitions:
        rules._extractor(definition)

    assert len(rules._extractor_cache) == 6
    assert rules._extractor_cache_bytes <= rules.DEFAULT_EXTRACTOR_CACHE_MAX_BYTES


@pytest.mark.usefixtures("empty_extractor_cache")
def test_extractor_cache_serializes_compilation_and_respects_budget(monkeypatch):
    start = Barrier(8)
    monkeypatch.setattr(rules, "_extractor_cache_max_bytes", 4 * (100 + sys.getsizeof("a")))

    def compile_rules(value, **_):
        time.sleep(0.02)
        return SimpleNamespace(key=value, reserved_bytes=100)

    constructor = Mock(side_effect=compile_rules)
    monkeypatch.setattr(rules, "get_privacy_extractor_class", lambda: constructor)

    def lookup(key):
        start.wait(timeout=5)
        return rules._extractor(key)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lookup, ["a", "b", "c", "d"] * 2))
    assert all(first is second for first, second in zip(results[:4], results[4:], strict=True))
    assert constructor.call_count == 4
    assert rules._extractor_cache_bytes <= rules._extractor_cache_max_bytes


@pytest.mark.usefixtures("empty_extractor_cache")
def test_rejected_rules_do_not_poison_cached_extractors(monkeypatch):
    engine = SimpleNamespace(reserved_bytes=0)

    def compile_rules(value):
        if value != "[]":
            raise ValueError("invalid rules")
        return engine

    constructor = Mock(side_effect=compile_rules)
    monkeypatch.setattr(rules, "get_privacy_extractor_class", lambda: constructor)
    assert rules._extractor() is engine
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid rules"):
            rules._extractor("invalid")
        assert rules._extractor() is engine
    assert constructor.call_count == 3
