# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.privacy.reference import build_privacy_field_reference
from openviking.privacy.skill_extractor import SkillPrivacyExtractionResult
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


async def test_user_and_public_content_writes_replace_registered_privacy_values(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "registered-custom-value"
    await service.privacy_configs.upsert(
        ctx,
        "custom",
        "external-system",
        {"not_a_conventional_secret_name": secret},
        updated_by=ctx.user.user_id,
    )
    reference = build_privacy_field_reference(
        ctx.user.user_id,
        "custom",
        "external-system",
        "not_a_conventional_secret_name",
    )

    private_uri = f"viking://user/{ctx.user.user_id}/privacy-sanitized.md"
    public_uri = "viking://resources/privacy-sanitized.md"
    for uri in (private_uri, public_uri):
        await service.fs.write(uri, f"credential={secret}", ctx=ctx, wait=False)
        stored = await service.viking_fs.read_file(uri, ctx=ctx)
        assert secret not in stored
        assert stored == f"credential={reference}"


async def test_service_components_share_one_privacy_sanitizer(service):
    assert service.privacy_sanitizer is not None
    assert service.fs._privacy_sanitizer is service.privacy_sanitizer
    assert service._skill_processor._privacy_sanitizer is service.privacy_sanitizer
    assert service._resource_processor._privacy_sanitizer is service.privacy_sanitizer


async def test_skill_description_is_sanitized_before_privacy_vlm(
    service,
    monkeypatch,
):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "registered-description-value"
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "description-service",
        {"key": secret},
        updated_by=ctx.user.user_id,
    )
    captured: dict[str, str] = {}

    async def fake_extract(**kwargs):
        captured["description"] = kwargs["skill_description"]
        return SkillPrivacyExtractionResult(
            values={},
            original_content=kwargs["content"],
            sanitized_content=kwargs["content"],
        )

    monkeypatch.setattr(
        "openviking.utils.skill_processor.extract_skill_privacy_values",
        fake_extract,
    )
    prepared, values = await service._skill_processor.prepare_skill_privacy(
        {
            "name": "description-privacy",
            "description": f"token={secret}",
            "content": "No credentials in the body.",
        },
        ctx,
    )

    reference = build_privacy_field_reference(
        ctx.user.user_id,
        "credential",
        "description-service",
        "key",
    )
    assert captured["description"] == f"token={reference}"
    assert prepared["description"] == f"token={reference}"
    assert values == {}


async def test_resource_artifact_sanitizes_user_dotfiles_but_skips_internal_sidecars(
    service,
    monkeypatch,
):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "registered-dotfile-value"
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "dotfile-service",
        {"key": secret},
        updated_by=ctx.user.user_id,
    )

    class ArtifactStore:
        def __init__(self):
            self.writes: dict[str, bytes] = {}

        async def list(self, _artifact_ref, _directory):
            return [
                SimpleNamespace(is_dir=False, name=".env", rel_path=".env"),
                SimpleNamespace(
                    is_dir=False,
                    name=".artifact_manifest.json",
                    rel_path=".artifact_manifest.json",
                ),
            ]

        async def read_bytes(self, _artifact_ref, path):
            if path == ".artifact_manifest.json":
                raise AssertionError("internal sidecar must not be sanitized")
            return f"TOKEN={secret}".encode()

        async def write_bytes(self, _artifact_ref, path, data):
            self.writes[path] = data

    async def read_manifest(_store, _artifact_ref):
        return {}

    async def write_manifest(_store, _artifact_ref, _manifest):
        return None

    monkeypatch.setattr("openviking.parse.output.read_artifact_manifest", read_manifest)
    monkeypatch.setattr("openviking.parse.output.write_artifact_manifest", write_manifest)
    store = ArtifactStore()
    await service._resource_processor._sanitize_parse_artifact(
        store=store,
        artifact_ref=SimpleNamespace(root_type="dir"),
        doc_rel="",
        target_root_uri="viking://resources/dotfile-test",
        ctx=ctx,
    )

    rewritten = store.writes[".env"].decode()
    assert secret not in rewritten
    assert build_privacy_field_reference(
        ctx.user.user_id,
        "credential",
        "dotfile-service",
        "key",
    ) in rewritten


async def test_content_write_rejects_registered_value_with_ambiguous_references(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    duplicate = "same-registered-value"
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "first",
        {"key": duplicate},
        updated_by=ctx.user.user_id,
    )
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "second",
        {"key": duplicate},
        updated_by=ctx.user.user_id,
    )

    with pytest.raises(InvalidArgumentError, match="mapped to multiple fields") as exc_info:
        await service.fs.write(
            f"viking://user/{ctx.user.user_id}/ambiguous.md",
            duplicate,
            ctx=ctx,
            wait=False,
        )
    details = exc_info.value.details
    assert details is not None
    assert len(details["candidate_references"][0]) == 2
    assert duplicate not in str(details)

    # Legacy content may predate write-time sanitization. The visible read path
    # must fail closed without choosing either field or returning the value.
    legacy_uri = f"viking://user/{ctx.user.user_id}/legacy-ambiguous.md"
    legacy_path = service.viking_fs._uri_to_path(legacy_uri, ctx=ctx)
    await service.viking_fs._async_agfs.write(legacy_path, duplicate.encode("utf-8"))
    assert await service.fs.read_visible(legacy_uri, ctx=ctx) == "{{ov_privacy:ambiguous}}"


async def test_sanitizer_uses_only_the_writing_users_privacy_catalog(service):
    owner = RequestContext(user=service.user, role=Role.USER)
    other = RequestContext(
        user=UserIdentifier(owner.account_id, "other-writer"),
        role=Role.USER,
    )
    owner_secret = "owner-only-registered-value"
    await service.initialize_user_directories(other)
    await service.privacy_configs.upsert(
        owner,
        "credential",
        "owner-service",
        {"key": owner_secret},
        updated_by=owner.user.user_id,
    )

    uri = "viking://resources/other-writer-content.md"
    await service.fs.write(uri, owner_secret, ctx=other, wait=False)
    assert await service.viking_fs.read_file(uri, ctx=other) == owner_secret


async def test_visible_read_sanitizes_legacy_content_that_bypassed_content_write(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "legacy-registered-value"
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "legacy-service",
        {"key": secret},
        updated_by=ctx.user.user_id,
    )
    uri = f"viking://user/{ctx.user.user_id}/legacy-content.md"
    raw_path = service.viking_fs._uri_to_path(uri, ctx=ctx)
    await service.viking_fs._async_agfs.write(
        raw_path,
        f"before {secret} after".encode("utf-8"),
    )

    visible = await service.fs.read_visible(uri, ctx=ctx)
    assert secret not in visible
    assert visible == (
        "before "
        + build_privacy_field_reference(
            ctx.user.user_id,
            "credential",
            "legacy-service",
            "key",
        )
        + " after"
    )


async def test_resource_import_sanitizes_parse_artifact_before_persistence(service, tmp_path):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "resource-import-registered-value"
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "import-service",
        {"custom": secret},
        updated_by=ctx.user.user_id,
    )
    source = tmp_path / "privacy-resource.md"
    source.write_text(f"# Imported\n\ncredential={secret}\n", encoding="utf-8")

    result = await service.resources.add_resource(
        path=str(source),
        ctx=ctx,
        wait=True,
    )
    assert result["status"] == "success"
    root_uri = result["root_uri"]
    stat = await service.viking_fs.stat(root_uri, ctx=ctx, skip_count=True)
    uris = [root_uri]
    if stat.get("isDir"):
        uris = [
            entry["uri"]
            for entry in await service.viking_fs.tree(
                root_uri,
                output="original",
                show_all_hidden=True,
                node_limit=None,
                level_limit=None,
                ctx=ctx,
            )
            if not entry.get("isDir") and not entry["name"].startswith(".")
        ]
    stored = "\n".join(
        [await service.viking_fs.read_file(uri, ctx=ctx) for uri in uris]
    )
    assert secret not in stored
    assert build_privacy_field_reference(
        ctx.user.user_id,
        "credential",
        "import-service",
        "custom",
    ) in stored
