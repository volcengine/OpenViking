# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

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
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError
from openviking_cli.session.user_id import UserIdentifier


class _PrivacyCatalog:
    def __init__(self, configs=None):
        self.configs = configs or {}
        self.revision = 0
        self.credentials_revision = 0
        self.versions = {}
        self.build_reads = 0
        self.settings = ResolvedPrivacyConfig(mode="registered")

    async def get_settings(self, _ctx):
        return self.settings

    def credential_revision(self, _ctx):
        return self.credentials_revision

    async def list_targets(self, _ctx, category):
        self.build_reads += 1
        return sorted(self.configs.get(category, {}))

    async def get_current(self, _ctx, category, target_key):
        values = self.configs.get(category, {}).get(target_key)
        return (
            None
            if values is None
            else SimpleNamespace(
                values=values,
                version=self.versions.get((category, target_key), self.revision),
                created_at="",
            )
        )


def _ctx(user_id="alice"):
    return RequestContext(user=UserIdentifier("account-a", user_id), role=Role.USER)


def test_privacy_reference_roundtrip_encodes_individual_segments():
    rendered = build_privacy_field_reference("alice", "custom-config", "prod-key", "api/key")
    assert rendered == (
        "{{ov_privacy:alice:viking://~/privacy/custom-config/prod-key#/values/api%2Fkey}}"
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


async def test_matching_requires_complete_format_or_explicit_credential():
    secret = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    catalog = _PrivacyCatalog(
        {
            "session": {
                "legacy": {"word": "ordinary", "embedded": f"key={secret}", "valid": secret}
            },
            "credential": {
                "manual": {
                    "five_ascii": "aB123",
                    "six_ascii": "aB1234",
                    "five_unicode": "🙂a1中文",
                    "six_unicode": "🙂a1中文测",
                    "empty": "",
                    "number": 42,
                }
            },
        }
    )
    sanitizer = PrivacySanitizer(catalog)
    content = f"ordinary aB123 aB1234 🙂a1中文 🙂a1中文测 {secret} 42"
    result = await sanitizer.sanitize(
        content,
        ctx=_ctx(),
        uri="viking://user/alice/sessions/legacy/messages.jsonl",
    )
    ascii_reference = build_privacy_field_reference("alice", "credential", "manual", "six_ascii")
    unicode_reference = build_privacy_field_reference(
        "alice", "credential", "manual", "six_unicode"
    )
    detected = build_privacy_field_reference("alice", "session", "legacy", "valid")
    assert (
        result.content
        == f"ordinary aB123 {ascii_reference} 🙂a1中文 {unicode_reference} {detected} 42"
    )
    query = await sanitizer.prepare_matcher(_ctx(), scope=("session", "legacy"))
    assert [(content[m.start : m.end], m.reference) for m in query(content)] == [
        ("aB1234", ascii_reference),
        ("🙂a1中文测", unicode_reference),
        (secret, detected),
    ]


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
    with pytest.raises(ResourceExhaustedError, match="size limit"):
        await PrivacySanitizer(catalog, max_content_bytes=5).sanitize("secret", ctx=_ctx())

    sanitizer = PrivacySanitizer(catalog, max_replacements=2)
    uri = "viking://user/alice/sessions/current/content.json"
    allowed, overflow = '["secret", "secret"]', '["secret", "secret", "secret"]'
    with pytest.raises(ResourceExhaustedError, match="replacement limit"):
        await sanitizer.sanitize(overflow, ctx=_ctx(), uri=uri)
    first, second = await asyncio.gather(
        sanitizer.sanitize(allowed, ctx=_ctx(), uri=uri),
        sanitizer.sanitize(allowed, ctx=_ctx(), uri=uri),
    )
    reference = build_privacy_field_reference("alice", "credential", "service", "key")
    assert json.loads(first.content) == json.loads(second.content) == [reference, reference]


async def test_sanitizer_cache_refreshes_after_credential_changes():
    first = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    second = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    catalog = _PrivacyCatalog(
        {
            "credential": {"service": {"key": "managed-secret"}},
            "session": {"current": {"key": first}},
        }
    )
    sanitizer = PrivacySanitizer(catalog)
    shared = build_privacy_field_reference("alice", "credential", "service", "key")
    local = build_privacy_field_reference("alice", "session", "current", "key")
    uri = "viking://user/alice/sessions/current/output.txt"
    assert (
        await sanitizer.sanitize("managed-secret " + first, ctx=_ctx(), uri=uri)
    ).content == shared + " " + local
    catalog.configs["session"]["current"] = {"key": second}
    catalog.versions["session", "current"] = 1
    assert (
        await sanitizer.sanitize("managed-secret " + second, ctx=_ctx(), uri=uri)
    ).content == shared + " " + local
    await sanitizer.prepare_matcher(_ctx(), scope=("session", "another"))
    assert catalog.build_reads == 1  # New Session fields must not reread the Credential catalog.

    catalog.configs["credential"]["service"]["key"] = "rotated-secret"
    catalog.credentials_revision += 1
    assert (
        await sanitizer.sanitize("managed-secret rotated-secret", ctx=_ctx(), uri=uri)
    ).content == "managed-secret " + shared
    assert catalog.build_reads == 2
    catalog.settings = ResolvedPrivacyConfig(mode="registered", never_mask=["rotated-secret"])
    assert (
        await sanitizer.sanitize("rotated-secret", ctx=_ctx(), uri=uri)
    ).content == "rotated-secret"


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
    result = await PrivacySanitizer(catalog).sanitize(
        "secret", ctx=_ctx(), uri="viking://user/alice/privacy/credential/service/current.json"
    )
    assert result.content == "secret" and not result.changed
    assert catalog.build_reads == 0


async def test_prepared_matcher_returns_original_offsets_in_worker_thread():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret", "long_key": "secrets"}}})
    find = await PrivacySanitizer(catalog).prepare_matcher(_ctx())
    reference = build_privacy_field_reference("alice", "credential", "service", "key")
    text = f"前缀🙂secrets {reference} secret"

    matches = await asyncio.to_thread(find, text)

    assert [(item.start, item.end, item.reference) for item in matches] == [
        (3, 10, build_privacy_field_reference("alice", "credential", "service", "long_key")),
        (len(text) - 6, len(text), reference),
    ]


@pytest.mark.parametrize("text", ["abcdef", "中文"])
async def test_prepared_matcher_enforces_utf8_byte_limit_with_empty_catalog(text):
    find = await PrivacySanitizer(_PrivacyCatalog(), max_content_bytes=6).prepare_matcher(_ctx())

    assert find(text) == []
    with pytest.raises(ResourceExhaustedError, match="size limit"):
        find(text + "a")


async def test_canonical_reference_is_stable_strict_and_tracks_independent_rotation(
    monkeypatch,
):
    info = Mock()
    monkeypatch.setattr("openviking.privacy.sanitizer.logger.info", info)
    secret = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    catalog = _PrivacyCatalog(
        {
            "credential": {"managed": {"b": secret, "a": secret}},
        }
    )
    sanitizer = PrivacySanitizer(catalog)
    chosen = build_privacy_field_reference("alice", "credential", "managed", "a")
    other = build_privacy_field_reference("alice", "credential", "managed", "b")
    result = await sanitizer.sanitize(f"{secret} {secret}", ctx=_ctx())
    assert result.content == f"{chosen} {chosen}"
    assert len(result.ambiguous_references) == 1
    info.assert_called()
    assert secret not in str(info.call_args)
    query = await sanitizer.prepare_matcher(_ctx())
    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
    with pytest.raises(InvalidArgumentError, match="multiple fields"):
        await asyncio.to_thread(query, secret)
    monkeypatch.delenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT")
    catalog.configs["credential"]["managed"]["a"] = "rotated-secret"
    catalog.credentials_revision += 1
    result = await sanitizer.sanitize(f"{secret} rotated-secret", ctx=_ctx())
    assert result.content == f"{other} {chosen}"


async def test_scope_precedes_ambiguity_and_never_rebinds_explicit_references(monkeypatch):
    category = "session"
    secret = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    catalog = _PrivacyCatalog(
        {
            "credential": {"managed": {"key": secret}},
            category: {
                "current": {"local001": secret, "local002": secret},
                "other": {"other001": secret},
            },
            "custom": {"legacy": {"key": secret}},
        }
    )
    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
    sanitizer = PrivacySanitizer(catalog)
    uri = "viking://user/alice/sessions/current/tool-results/job/output.txt"
    explicit = build_privacy_field_reference("alice", category, "other", "other001")
    managed = build_privacy_field_reference("alice", "credential", "managed", "key")
    result = await sanitizer.sanitize(secret + " " + explicit, ctx=_ctx(), uri=uri)
    assert result.content == managed + " " + explicit
    assert not result.ambiguous_references

    del catalog.configs["credential"]
    catalog.credentials_revision += 1
    with pytest.raises(InvalidArgumentError, match="multiple fields"):
        await sanitizer.sanitize(secret, ctx=_ctx(), uri=uri)
    monkeypatch.delenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT")
    local = build_privacy_field_reference("alice", category, "current", "local001")
    result = await sanitizer.sanitize(secret, ctx=_ctx(), uri=uri)
    assert result.content == local
    assert result.ambiguous_references == (
        (local, build_privacy_field_reference("alice", category, "current", "local002")),
    )
    alias = uri.replace("viking://user/alice/", "viking://~/")
    assert (await sanitizer.sanitize(secret, ctx=_ctx(), uri=alias)).content == local
    for outside in (
        uri.replace("/alice/", "/bob/"),
        uri.replace("viking://user/alice/", "viking://resources/"),
    ):
        assert (await sanitizer.sanitize(secret, ctx=_ctx(), uri=outside)).content == secret

    del catalog.configs[category]["current"]
    catalog.revision += 1
    assert (await sanitizer.sanitize(secret, ctx=_ctx(), uri=uri)).content == secret
