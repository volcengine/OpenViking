# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
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
        self.versions = {}
        self.build_reads = 0
        self.settings = ResolvedPrivacyConfig(mode="registered")

    async def get_settings(self, _ctx):
        return self.settings

    def catalog_revision(self, _ctx):
        return self.revision

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
            "credential": {"manual": {"short": "pin", "empty": "", "number": 42}},
        }
    )
    result = await PrivacySanitizer(catalog).sanitize(
        f"ordinary pin {secret} 42",
        ctx=_ctx(),
        uri="viking://user/alice/sessions/legacy/messages.jsonl",
    )
    manual = build_privacy_field_reference("alice", "credential", "manual", "short")
    detected = build_privacy_field_reference("alice", "session", "legacy", "valid")
    assert result.content == f"ordinary {manual} {detected} 42"


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


async def test_sanitizer_cache_refreshes_after_credential_rotation():
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


async def test_waiting_matcher_reuses_the_latest_catalog_revision():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "first1"}}})
    sanitizer = PrivacySanitizer(catalog)
    async with sanitizer._cache_lock:
        pending = asyncio.create_task(sanitizer.prepare_matcher(_ctx()))
        await asyncio.sleep(0)
        catalog.configs["credential"]["service"]["key"] = "second2"
        catalog.revision += 1

    await pending
    find = await sanitizer.prepare_matcher(_ctx())
    assert [m.reference for m in find("second2")] == [
        build_privacy_field_reference("alice", "credential", "service", "key")
    ]
    assert catalog.build_reads == 1


async def test_waiting_matcher_refreshes_object_snapshot():
    first = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    second = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    catalog = _PrivacyCatalog({"session": {"current": {"key": first}}})
    catalog.versions = {("session", "current"): 1}
    sanitizer = PrivacySanitizer(catalog)
    async with sanitizer._cache_lock:
        pending = asyncio.create_task(
            sanitizer.prepare_matcher(_ctx(), scope=("session", "current"))
        )
        await asyncio.sleep(0)
        # Another worker rotates this object without changing the local revision.
        catalog.configs["session"]["current"] = {"key": second}
        catalog.versions["session", "current"] = 2
    find = await pending
    reference = build_privacy_field_reference("alice", "session", "current", "key")
    assert not find(first) and [match.reference for match in find(second)] == [reference]


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


@pytest.mark.parametrize(
    "path",
    [
        "privacy/credential/service/current.json",
        "settings/user_config.json",
        "settings/user_config.backup.json",
    ],
)
async def test_sanitizer_never_rewrites_privacy_or_settings_storage(path):
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret"}}})
    sanitizer = PrivacySanitizer(catalog)
    result = await sanitizer.sanitize(
        "secret",
        ctx=_ctx(),
        uri=f"viking://user/alice/{path}",
    )
    assert result.content == "secret"
    assert not result.changed
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


async def test_prepared_matcher_rejects_dense_matches():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret"}}})
    find = await PrivacySanitizer(catalog, max_replacements=2).prepare_matcher(_ctx())
    assert len(find("secret secret")) == 2
    with pytest.raises(ResourceExhaustedError, match="replacement limit"):
        find("secret secret secret")


async def test_prepared_matcher_rejects_unpaired_surrogates():
    find = await PrivacySanitizer(_PrivacyCatalog()).prepare_matcher(_ctx())

    with pytest.raises(UnicodeEncodeError):
        find("invalid \ud800")


async def test_canonical_reference_is_stable_strict_and_tracks_independent_rotation(
    monkeypatch,
):
    warning = Mock()
    monkeypatch.setattr("openviking.privacy.sanitizer.logger.warning", warning)
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
    warning.assert_called()
    assert secret not in str(warning.call_args)
    query = await sanitizer.prepare_matcher(_ctx())
    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
    with pytest.raises(InvalidArgumentError, match="multiple fields"):
        await asyncio.to_thread(query, secret)
    monkeypatch.delenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT")
    catalog.configs["credential"]["managed"]["a"] = "rotated-secret"
    catalog.revision += 1
    result = await sanitizer.sanitize(f"{secret} rotated-secret", ctx=_ctx())
    assert result.content == f"{other} {chosen}"


async def test_never_mask_refreshes_cached_matching():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "managed-secret"}}})
    sanitizer = PrivacySanitizer(catalog)
    assert (await sanitizer.sanitize("managed-secret", ctx=_ctx())).changed

    catalog.settings = ResolvedPrivacyConfig(mode="registered", never_mask=["managed-secret"])
    result = await sanitizer.sanitize("managed-secret", ctx=_ctx())
    assert result.content == "managed-secret"
    catalog.settings = ResolvedPrivacyConfig(mode="registered")
    assert (await sanitizer.sanitize("managed-secret", ctx=_ctx())).changed


@pytest.mark.parametrize("category", ["session", "skill"])
async def test_scope_precedes_ambiguity_and_never_rebinds_explicit_references(
    category, monkeypatch
):
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
    uri = (
        "viking://user/alice/sessions/current/tool-results/job/output.txt"
        if category == "session"
        else "viking://user/alice/skills/current/assets/settings.env"
    )
    explicit = build_privacy_field_reference("alice", category, "other", "other001")
    managed = build_privacy_field_reference("alice", "credential", "managed", "key")
    result = await sanitizer.sanitize(secret + " " + explicit, ctx=_ctx(), uri=uri)
    assert result.content == managed + " " + explicit
    assert not result.ambiguous_references

    del catalog.configs["credential"]
    catalog.revision += 1
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
    if category == "skill":
        shared = "viking://agent/skills/current/SKILL.md"
        assert (await sanitizer.sanitize(secret, ctx=_ctx(), uri=shared)).content == local
    for outside in (
        uri.replace("/alice/", "/bob/"),
        uri.replace("viking://user/alice/", "viking://resources/"),
    ):
        assert (await sanitizer.sanitize(secret, ctx=_ctx(), uri=outside)).content == secret

    del catalog.configs[category]["current"]
    catalog.revision += 1
    assert (await sanitizer.sanitize(secret, ctx=_ctx(), uri=uri)).content == secret


async def test_word_boundaries_preserve_identifiers_and_allow_shorter_valid_overlap():
    catalog = _PrivacyCatalog(
        {"credential": {"service": {"short": "pass123", "long": "pass123-v2"}}}
    )
    sanitizer = PrivacySanitizer(catalog)
    short = build_privacy_field_reference("alice", "credential", "service", "short")
    long = build_privacy_field_reference("alice", "credential", "service", "long")
    result = await sanitizer.sanitize(
        "pass123-v2x pass123-v2 xpass123 pass123² 中pass123 pass123_", ctx=_ctx()
    )
    assert result.content == f"{short}-v2x {long} xpass123 pass123² 中pass123 pass123_"
    query = await sanitizer.prepare_matcher(_ctx())
    assert [(m.start, m.end, m.reference) for m in query("pass123-v2x")] == [(0, 7, short)]


async def test_invalid_candidates_do_not_consume_the_replacement_budget():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "managed-secret"}}})
    sanitizer = PrivacySanitizer(catalog, max_replacements=1)
    ordinary = "xmanaged-secretx " * 12000
    reference = build_privacy_field_reference("alice", "credential", "service", "key")
    result = await sanitizer.sanitize("managed-secret " + ordinary, ctx=_ctx())
    assert result.content == reference + " " + ordinary
    assert len(result.replacements) == 1


async def test_skill_frontmatter_is_preserved_and_spans_address_original_text():
    catalog = _PrivacyCatalog({"credential": {"managed": {"key": "managed-secret"}}})
    sanitizer = PrivacySanitizer(catalog)
    header = '---\nname: demo\ndescription: "标题🙂 managed-secret"\n---\n\n'
    body = "---\nexample: managed-secret\n---\n正文🙂 managed-secret\n"
    result = await sanitizer.sanitize(
        header + body, ctx=_ctx(), uri="viking://user/alice/skills/demo/SKILL.md"
    )
    reference = build_privacy_field_reference("alice", "credential", "managed", "key")
    assert result.content == header + body.replace("managed-secret", reference)
    assert [(header + body)[m.start : m.end] for m in result.replacements] == [
        "managed-secret",
        "managed-secret",
    ]
    text = await sanitizer.sanitize(
        header + body, ctx=_ctx(), uri="viking://user/alice/skills/demo/"
    )
    assert text.content == (header + body).replace("managed-secret", reference)


async def test_automatic_matching_keeps_foreign_content_and_private_config_untouched():
    catalog = _PrivacyCatalog({"credential": {"service": {"key": "secret"}}})
    catalog.settings = ResolvedPrivacyConfig(mode="auto_strict")
    sanitizer = PrivacySanitizer(catalog)
    for uri in (
        "viking://user/bob/sessions/current/output.txt",
        "viking://~/privacy/credential/service/current.json",
        "viking://~/settings/user_config.json",
    ):
        assert await sanitizer.sanitize_for_write("secret", ctx=_ctx(), uri=uri) == "secret"
