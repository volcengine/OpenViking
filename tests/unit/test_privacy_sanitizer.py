# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.privacy.reference import (
    PrivacyFieldReference,
    build_privacy_field_reference,
    parse_privacy_field_reference,
    parse_privacy_field_selector,
    parse_privacy_target_selector,
)
from openviking.privacy.sanitizer import PrivacySanitizer
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import ResourceExhaustedError
from openviking_cli.session.user_id import UserIdentifier


class _PrivacyCatalog:
    def __init__(self, configs=None):
        self.configs = configs or {}
        self.revision = 0
        self.build_reads = 0

    def catalog_revision(self, _ctx):
        return self.revision

    async def list_categories(self, _ctx):
        self.build_reads += 1
        return sorted(self.configs)

    async def list_targets(self, _ctx, category):
        return sorted(self.configs.get(category, {}))

    async def get_current(self, _ctx, category, target_key):
        values = self.configs.get(category, {}).get(target_key)
        return None if values is None else SimpleNamespace(values=values)


def _ctx(user_id="alice"):
    return RequestContext(user=UserIdentifier("account-a", user_id), role=Role.USER)


def test_privacy_sanitizer_uses_native_ragfs_matcher():
    sanitizer = PrivacySanitizer(_PrivacyCatalog())
    assert sanitizer._matcher_class.__module__ == "ragfs_python"
    assert sanitizer._matcher_class.__name__ == "PrivacyMatcher"


def test_privacy_reference_roundtrip_encodes_individual_segments():
    rendered = build_privacy_field_reference("alice", "custom-config", "prod-key", "api/key")
    assert rendered == (
        "{{ov_privacy:alice:viking://~/privacy/custom-config/"
        "prod-key#/values/api%2Fkey}}"
    )
    assert parse_privacy_field_reference(rendered) == PrivacyFieldReference(
        owner_user_id="alice",
        category="custom-config",
        target_key="prod-key",
        field="api/key",
    )
    spaced = rendered.replace("{{ov_privacy:", "{{  ov_privacy:").replace("}}", " \t}}")
    parsed_spaced = parse_privacy_field_reference(spaced)
    assert parsed_spaced is not None
    assert parsed_spaced.render() == rendered
    assert parse_privacy_field_reference(f"prefix {rendered}") is None
    assert parse_privacy_field_reference(rendered.replace(":alice:", ": alice:")) is None
    assert parse_privacy_field_reference(rendered.replace("privacy/", "privacy/ ")) is None
    assert parse_privacy_field_reference(rendered.replace("{{", "{{\n")) is None
    assert parse_privacy_field_selector(
        "viking://~/privacy/custom-config/prod-key#/values/api%2Fkey",
        current_user_id="alice",
    ) == PrivacyFieldReference(
        owner_user_id="alice",
        category="custom-config",
        target_key="prod-key",
        field="api/key",
    )
    target = parse_privacy_target_selector(
        "viking://user/alice/privacy/custom-config/prod-key",
        current_user_id="other",
    )
    assert target is not None
    assert target.owner_user_id == "alice"
    assert target.category == "custom-config"
    assert target.target_key == "prod-key"


async def test_sanitizer_protects_registered_strings_at_least_six_chars_without_field_guessing():
    catalog = _PrivacyCatalog(
        {
            "custom": {
                "service": {
                    "five_chars": "abcde",
                    "six_chars": "abcdef",
                    "base_url": "https://private.example",
                    "empty": "",
                    "numeric": 42,
                }
            }
        }
    )
    sanitizer = PrivacySanitizer(catalog)

    result = await sanitizer.sanitize(
        "short=abcde token=abcdef endpoint=https://private.example empty= numeric=42",
        ctx=_ctx(),
        uri="viking://user/alice/memories/preferences/item.md",
    )

    assert result.changed
    assert "short=abcde" in result.content
    assert "token={{ov_privacy:alice:viking://~/privacy/custom/service#/values/six_chars}}" in (
        result.content
    )
    assert (
        "endpoint={{ov_privacy:alice:viking://~/privacy/custom/service#/values/base_url}}"
        in result.content
    )
    assert "numeric=42" in result.content


async def test_sanitizer_prefers_longest_overlap_and_does_not_replace_ambiguous_values():
    catalog = _PrivacyCatalog(
        {
            "credential": {
                "short": {"value": "abc"},
                "long": {"value": "abcdef"},
                "duplicate-a": {"value": "same-secret"},
                "duplicate-b": {"other": "same-secret"},
            }
        }
    )
    sanitizer = PrivacySanitizer(catalog)

    result = await sanitizer.sanitize("abcdef same-secret", ctx=_ctx())

    assert result.content.startswith(
        "{{ov_privacy:alice:viking://~/privacy/credential/long#/values/value}}"
    )
    assert result.content.endswith(" {{ov_privacy:ambiguous}}")
    assert len(result.replacements) == 2
    assert len(result.ambiguous_references) == 1
    assert len(result.ambiguous_references[0]) == 2


async def test_sanitizer_bounds_dense_match_metadata_for_scans():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret"}}})
    sanitizer = PrivacySanitizer(catalog)

    result = await sanitizer.sanitize(
        "secret secret secret",
        ctx=_ctx(),
        max_replacements=2,
        fail_on_match_limit=False,
    )

    assert result.match_limit_exceeded
    assert result.content == "secret secret secret"
    assert not result.replacements


async def test_sanitizer_rejects_oversized_or_excessively_dense_writes():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret"}}})

    size_limited = PrivacySanitizer(catalog, max_content_bytes=5)
    with pytest.raises(ResourceExhaustedError, match="size limit"):
        await size_limited.sanitize("secret", ctx=_ctx())

    match_limited = PrivacySanitizer(catalog, max_replacements=2)
    with pytest.raises(ResourceExhaustedError, match="replacement limit"):
        await match_limited.sanitize("secret secret secret", ctx=_ctx())


async def test_sanitizer_cache_rebuilds_only_after_catalog_revision_changes():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "first1"}}})
    sanitizer = PrivacySanitizer(catalog)

    first = await sanitizer.sanitize("first1", ctx=_ctx())
    second = await sanitizer.sanitize("first1", ctx=_ctx())
    assert first.changed and second.changed
    assert catalog.build_reads == 1

    catalog.configs["credential"]["service"]["key"] = "second2"
    catalog.revision += 1
    rebuilt = await sanitizer.sanitize("first1 second2", ctx=_ctx())
    assert rebuilt.content.startswith("first1 ")
    assert rebuilt.content.endswith(
        "{{ov_privacy:alice:viking://~/privacy/credential/service#/values/key}}"
    )
    assert catalog.build_reads == 2


async def test_sanitizer_cache_expires_after_ttl_even_without_local_revision_change():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "first1"}}})
    now = [100.0]
    sanitizer = PrivacySanitizer(
        catalog,
        cache_ttl_seconds=3600,
        monotonic_clock=lambda: now[0],
    )

    initial = await sanitizer.sanitize("first1", ctx=_ctx())
    assert initial.changed
    assert catalog.build_reads == 1

    # Simulate a change made by another worker: the local revision does not move.
    catalog.configs["credential"]["service"]["key"] = "second2"
    now[0] += 3599
    stale = await sanitizer.sanitize("first1 second2", ctx=_ctx())
    assert stale.content.endswith(" second2")
    assert catalog.build_reads == 1

    now[0] += 1
    refreshed = await sanitizer.sanitize("first1 second2", ctx=_ctx())
    assert refreshed.content.startswith("first1 ")
    assert refreshed.content.endswith(
        "{{ov_privacy:alice:viking://~/privacy/credential/service#/values/key}}"
    )
    assert catalog.build_reads == 2


async def test_sanitizer_never_rewrites_privacy_storage_itself():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret"}}})
    sanitizer = PrivacySanitizer(catalog)
    result = await sanitizer.sanitize(
        "secret",
        ctx=_ctx(),
        uri="viking://user/alice/privacy/credential/service/current.json",
    )
    assert result.content == "secret"
    assert not result.changed
    assert catalog.build_reads == 0
