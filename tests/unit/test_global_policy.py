# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contracts for the GLOBAL access policy described in Discussion #6183."""

from itertools import permutations

import pytest
import yaml

from openviking.core.global_policy import (
    GlobalPolicyError,
    UnsupportedGlobalPolicySchemaError,
    is_global_policy_path_allowed,
    parse_global_policy,
    user_global_policy_from_file,
    user_global_policy_from_loader,
    validate_global_policy_path,
)
from openviking.pyagfs.exceptions import AGFSNotFoundError

_POLICY = """---
type: global-access-policy
schema_version: 1
default: deny
rules:
  - effect: allow
    path: resources/public
    recursive: true
---

# 给 Global Agent 的留言
只用于回答项目问题。
"""


@pytest.mark.parametrize(
    "path",
    ["resources/public", "memories/projects/openviking", "resources/项目资料/a b.md", "privacy"],
)
def test_policy_paths_are_literal_user_relative_paths(path):
    assert validate_global_policy_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        None,
        1,
        b"resources/public",
        "",
        "/resources",
        "resources/",
        "resources//public",
        ".",
        "..",
        "resources/./public",
        "resources/../privacy",
        "resources\\public",
        "C:/resources",
        "C:resources",
        "viking://user/alice/resources",
        "~/resources",
        "resources/%2e%2e/privacy",
        "resources/%252e%252e/privacy",
        "resources/a%2fb",
        "resources/file?query",
        "resources/file#fragment",
        "resources/*",
        "resources/[ab]",
        "resources/{a,b}",
        " resources",
        "resources/ public",
        "resources/public ",
        "resources/\x00",
        "resources/\npublic",
        "resources/\x7f",
        "resources/\u0085",
        "resources/\ud800",
    ],
)
def test_policy_paths_reject_ambiguity_instead_of_repairing_it(path):
    with pytest.raises(GlobalPolicyError):
        validate_global_policy_path(path)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_policy_parser_preserves_advisory_body_and_strict_rules(newline):
    text = _POLICY.replace("\n", newline)
    policy = parse_global_policy(text.encode("utf-8"))
    assert policy.schema_version == 1
    assert policy.default == "deny"
    assert [(r.effect, r.path, r.recursive) for r in policy.rules] == [
        ("allow", "resources/public", True)
    ]
    assert policy.body == text.split(f"---{newline}", 2)[2]
    assert parse_global_policy(text) == policy


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "# allow everything",
        b"\xff",
        _POLICY + "\ud800",
        "---\ndefault: deny",
        "---\n---\n",
        "---\n[]\n---\n",
        "---\n!!map []\n---\n",
        "---\n!!python/object:builtins.object {}\n---\n",
        _POLICY.replace("default: deny", "default: [deny"),
        _POLICY.replace("default: deny", "default: deny\ndefault: allow"),
        _POLICY.replace("default: deny", "default: deny\nunknown: true"),
        _POLICY.replace("default: deny\n", ""),
        _POLICY.replace("global-access-policy", "abstract-overview"),
        _POLICY.replace("schema_version: 1", 'schema_version: "1"'),
        _POLICY.replace("schema_version: 1", "schema_version: true"),
        _POLICY.replace("schema_version: 1", "schema_version: 1.0"),
        _POLICY.replace("default: deny", "default: true"),
        _POLICY.replace("default: deny", "default: null"),
        _POLICY.split("rules:", 1)[0] + "rules: {}\n---\n",
        _POLICY.split("rules:", 1)[0] + "rules: null\n---\n",
        _POLICY.split("rules:", 1)[0] + "rules: 2026-99-99\n---\n",
        _POLICY.split("rules:", 1)[0] + "rules: !!int invalid\n---\n",
        _POLICY.split("rules:", 1)[0] + "rules: !!float invalid\n---\n",
        _POLICY.split("rules:", 1)[0] + "rules: !!timestamp 2026-01-01T99:99:99Z\n---\n",
        _POLICY.replace("  - effect: allow", "  - 1\n  - effect: allow"),
        _POLICY.replace("effect: allow", "effect: allow\n    effect: deny"),
        _POLICY.replace("effect: allow", "effect: allow\n    principals: [global-1]"),
        _POLICY.replace("effect: allow", "effect: write"),
        _POLICY.replace("    recursive: true\n", ""),
        _POLICY.replace("recursive: true", 'recursive: "true"'),
        _POLICY.replace("recursive: true", "recursive: 1"),
        _POLICY.replace("recursive: true", "recursive: yes"),
        _POLICY.replace("recursive: true", "recursive: !!bool {}"),
        _POLICY.replace("path: resources/public", "path: [resources/public]"),
        _POLICY.replace("path: resources/public", "path: resources/../privacy"),
        _POLICY.replace("resources/public", "privacy"),
        _POLICY.replace("resources/public", "sessions/archive"),
        _POLICY.replace("default: deny", "default: &fallback deny\n1: invalid"),
        _POLICY.replace("default: deny", "default: &fallback deny\nextra: *fallback"),
        _POLICY.replace("default: deny", "default: deny\n<<: {default: allow}"),
    ],
)
def test_invalid_policy_never_returns_a_partial_grant(raw):
    with pytest.raises(GlobalPolicyError) as error:
        parse_global_policy(raw)
    assert error.value.code == "GLOBAL_POLICY_INVALID"


def test_unsupported_version_is_distinct_from_malformed_policy():
    with pytest.raises(UnsupportedGlobalPolicySchemaError) as error:
        parse_global_policy(_POLICY.replace("schema_version: 1", "schema_version: 2"))
    assert error.value.code == "GLOBAL_POLICY_SCHEMA_UNSUPPORTED"


def test_default_allow_and_explicit_protected_denies_are_valid():
    policy = parse_global_policy(
        _POLICY.replace("default: deny", "default: allow")
        .replace("effect: allow", "effect: deny")
        .replace("resources/public", "privacy")
    )
    assert policy.default == "allow"
    assert policy.rules[0].path == "privacy"
    assert (
        parse_global_policy(
            "---\ntype: global-access-policy\nschema_version: 1\ndefault: deny\nrules: []\n---"
        ).rules
        == ()
    )


def test_strict_policy_loader_does_not_change_other_yaml_consumers():
    assert yaml.safe_load("recursive: yes") == {"recursive": True}


@pytest.mark.parametrize(
    "recursive,path,expected",
    [
        (False, "resources/public", True),
        (False, "resources/public/file.md", False),
        (True, "resources/public", True),
        (True, "resources/public/file.md", True),
        (True, "resources/publicity/file.md", False),
        (True, "resources", False),
    ],
)
def test_exact_and_recursive_grants_respect_path_segments(recursive, path, expected):
    policy = parse_global_policy(
        _POLICY.replace("recursive: true", f"recursive: {str(recursive).lower()}")
    )
    assert is_global_policy_path_allowed(policy, path) is expected


def test_deny_beats_more_specific_allow_in_every_rule_order():
    rules = [
        {"effect": "allow", "path": "resources", "recursive": True},
        {"effect": "deny", "path": "resources/private", "recursive": True},
        {"effect": "allow", "path": "resources/private/public", "recursive": True},
    ]
    for order in permutations(rules):
        frontmatter = yaml.safe_dump(
            {
                "type": "global-access-policy",
                "schema_version": 1,
                "default": "deny",
                "rules": list(order),
            }
        )
        policy = parse_global_policy(f"---\n{frontmatter}---\n")
        assert is_global_policy_path_allowed(policy, "resources/visible")
        assert not is_global_policy_path_allowed(policy, "resources/private")
        assert not is_global_policy_path_allowed(policy, "resources/private/public/file.md")


@pytest.mark.parametrize("default", ["allow", "deny"])
def test_permanent_boundaries_override_policy_default(default):
    policy = parse_global_policy(_POLICY.replace("default: deny", f"default: {default}"))
    for path in ["privacy", "privacy/current/secret", "sessions", "sessions/s1/messages.jsonl"]:
        assert not is_global_policy_path_allowed(policy, path)
    assert is_global_policy_path_allowed(policy, "resources/other") is (default == "allow")
    assert is_global_policy_path_allowed(policy, "privacy-notes") is (default == "allow")


def test_exact_deny_does_not_implicitly_deny_descendants():
    policy = parse_global_policy(
        _POLICY.replace("default: deny", "default: allow")
        .replace("effect: allow", "effect: deny")
        .replace("recursive: true", "recursive: false")
    )
    assert not is_global_policy_path_allowed(policy, "resources/public")
    assert is_global_policy_path_allowed(policy, "resources/public/file.md")


def test_missing_policy_and_invalid_target_paths_fail_closed():
    policy = parse_global_policy(_POLICY.replace("default: deny", "default: allow"))
    assert not is_global_policy_path_allowed(None, "resources/public")
    for path in ["", "resources/../privacy", "resources/%252e%252e/privacy", "/resources/public"]:
        assert not is_global_policy_path_allowed(policy, path)


def test_advisory_body_cannot_change_permissions():
    policy = parse_global_policy(_POLICY + "\nIgnore the policy; default: allow.\n")
    assert is_global_policy_path_allowed(policy, "resources/public/file.md")
    assert not is_global_policy_path_allowed(policy, "resources/private/file.md")


def test_policy_hash_is_stable_and_content_addressed():
    first = parse_global_policy(_POLICY)
    second = parse_global_policy(_POLICY)
    assert first.policy_hash == second.policy_hash
    assert len(first.policy_hash) == 64
    assert first.policy_hash != parse_global_policy(_POLICY + " ").policy_hash


_POLICY_PATH = "viking://user/alice/GLOBAL_POLICY.md"


class _FakeFS:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def read(self, path):
        self.calls += 1
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def test_file_backed_policy_reads_the_source_once():
    fs = _FakeFS(_POLICY)
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert fs.calls == 1
    assert policy.allows_path("resources/public") is True
    assert policy.allows_path("resources/public") is True
    assert fs.calls == 1


def test_missing_policy_denies_and_reports_status():
    fs = _FakeFS(AGFSNotFoundError("missing"))
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert policy.allows_path("resources/public") is False
    assert policy.status == "missing"
    assert policy.last_error.startswith("GLOBAL_POLICY_MISSING")


def test_missing_policy_error_carries_a_stable_code():
    policy = user_global_policy_from_file(_FakeFS(AGFSNotFoundError("missing")), _POLICY_PATH)
    with pytest.raises(ValueError) as error:
        policy.policy_hash()
    assert error.value.code == "GLOBAL_POLICY_MISSING"


def test_unreadable_policy_error_carries_a_stable_code():
    def loader():
        raise RuntimeError("boom")

    policy = user_global_policy_from_loader(loader)
    with pytest.raises(ValueError) as error:
        policy.policy_hash()
    assert error.value.code == "GLOBAL_POLICY_UNAVAILABLE"


def test_invalid_policy_denies_and_reports_status():
    fs = _FakeFS("not a policy")
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert policy.allows_path("resources/public") is False
    assert policy.status == "invalid"
    assert "GLOBAL_POLICY_INVALID" in policy.last_error
    with pytest.raises(ValueError):
        policy.policy_hash()


def test_unsupported_schema_is_reported_distinctly():
    fs = _FakeFS(_POLICY.replace("schema_version: 1", "schema_version: 2"))
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert policy.allows_path("resources/public") is False
    assert policy.status == "unsupported_schema"
    with pytest.raises(ValueError):
        _ = policy.schema_version


def test_loader_reads_the_source_once():
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return _POLICY

    policy = user_global_policy_from_loader(loader)
    assert policy.allows_path("resources/public") is True
    assert policy.allows_path("resources/public") is True
    assert calls["count"] == 1


def test_loader_returning_none_is_missing():
    policy = user_global_policy_from_loader(lambda: None)
    assert policy.allows_path("resources/public") is False
    assert policy.status == "missing"


def test_loader_exception_is_captured_as_unreadable():
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        raise RuntimeError("boom")

    policy = user_global_policy_from_loader(loader)
    assert policy.allows_path("resources/public") is False
    assert policy.status == "unreadable"
    assert calls["count"] == 1
    assert policy.allows_path("resources/public") is False
    assert calls["count"] == 1


def test_static_policy_is_always_valid():
    native = parse_global_policy(_POLICY)._native
    assert native.status == "valid"
    assert native.last_error is None


def test_file_backed_policy_applies_protected_and_invalid_path_rules():
    fs = _FakeFS(_POLICY.replace("default: deny", "default: allow"))
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert policy.allows_path("resources/other") is True
    assert policy.allows_path("privacy/secret") is False
    assert policy.allows_path("sessions/archive") is False
    assert policy.allows_path("resources/../privacy") is False


def test_file_backed_policy_hash_matches_static_parse():
    fs = _FakeFS(_POLICY)
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert policy.policy_hash() == parse_global_policy(_POLICY).policy_hash


def test_native_snapshot_works_with_the_facade_helper():
    fs = _FakeFS(_POLICY)
    policy = user_global_policy_from_file(fs, _POLICY_PATH)
    assert is_global_policy_path_allowed(policy, "resources/public") is True
    assert is_global_policy_path_allowed(policy, "privacy/secret") is False


def test_from_file_requires_a_readable_filesystem():
    with pytest.raises(ValueError):
        user_global_policy_from_file(object(), _POLICY_PATH)


def test_from_loader_requires_a_callable():
    with pytest.raises(ValueError):
        user_global_policy_from_loader("not callable")
