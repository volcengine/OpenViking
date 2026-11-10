# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.privacy.reference import build_privacy_field_reference
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import patch_user_config
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
async def enabled_privacy(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    await patch_user_config(service.viking_fs, ctx, {"privacy": {"mode": "auto"}})


async def test_user_and_public_content_writes_replace_registered_privacy_values(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "registered-custom-value"
    await service.privacy_configs.upsert(
        ctx,
        "credential",
        "external-system",
        {"not_a_conventional_secret_name": secret},
        updated_by=ctx.user.user_id,
    )
    reference = build_privacy_field_reference(
        ctx.user.user_id,
        "credential",
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


@pytest.mark.parametrize("method", [None, "vlm"])
async def test_skill_preserves_metadata_and_protects_body_through_add_update_and_read(
    service, client, monkeypatch, method
):
    from openviking.core.skill_loader import SkillLoader
    from openviking.storage.abstract_overview import parse_abstract_overview

    if method is None:
        monkeypatch.delenv("OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD", raising=False)
    else:
        monkeypatch.setenv("OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD", method)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await patch_user_config(service.viking_fs, ctx, {"privacy": {"mode": "auto"}})
    secret = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    description = f"metadata managed-secret and {secret}"
    await service.privacy_configs.upsert(ctx, "credential", "managed", {"key": "managed-secret"})

    async def candidates(**_kwargs):
        return {"key": secret}

    monkeypatch.setattr("openviking.utils.skill_processor.extract_skill_privacy_values", candidates)
    for verb, prefix in [("post", "initial"), ("put", "updated")]:
        response = await getattr(client, verb)(
            "/api/v1/skills" + ("/metadata-skill" if verb == "put" else ""),
            json={
                "data": SkillLoader.to_skill_md(
                    {
                        "name": "metadata-skill",
                        "description": description,
                        "content": f'{prefix}🙂 managed-secret\napi_key="{secret}"',
                    }
                ),
                "wait": True,
            },
        )
        assert response.status_code == 200, response.text
        root = f"viking://user/{ctx.user.user_id}/skills/metadata-skill"
        for raw in [False, True]:
            shown = await client.get(
                "/api/v1/content/read", params={"uri": root + "/SKILL.md", "raw": raw}
            )
            assert shown.status_code == 200, shown.text
            skill = SkillLoader.parse(shown.json()["result"])
            assert skill["description"] == description
            assert (secret in skill["content"]) == raw
        stored = SkillLoader.parse(await service.viking_fs.read_file(root + "/SKILL.md", ctx=ctx))
        assert stored["description"] == description
        assert "managed-secret" not in stored["content"] and secret not in stored["content"]
        abstract = parse_abstract_overview(
            await service.viking_fs.read_file(root + "/.abstract.md", ctx=ctx)
        )
        assert description in abstract.body


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
    assert (
        build_privacy_field_reference(
            ctx.user.user_id,
            "credential",
            "dotfile-service",
            "key",
        )
        in rewritten
    )


async def test_content_write_and_legacy_reads_reject_ambiguity_in_strict_mode(service, monkeypatch):
    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
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
    with pytest.raises(InvalidArgumentError, match="mapped to multiple fields"):
        await service.fs.read_visible(legacy_uri, ctx=ctx)


async def test_sanitizer_uses_only_the_writing_users_privacy_catalog(service):
    owner = RequestContext(user=service.user, role=Role.USER)
    other = RequestContext(
        user=UserIdentifier(owner.account_id, "other-writer"),
        role=Role.USER,
    )
    owner_secret = "owner-only-registered-value"
    await service.initialize_user_directories(other)
    await patch_user_config(service.viking_fs, other, {"privacy": {"mode": "auto"}})
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
    stored = "\n".join([await service.viking_fs.read_file(uri, ctx=ctx) for uri in uris])
    assert secret not in stored
    assert (
        build_privacy_field_reference(
            ctx.user.user_id,
            "credential",
            "import-service",
            "custom",
        )
        in stored
    )


async def test_skill_finalizes_original_offsets_using_latest_fields(service, monkeypatch):
    from openviking.privacy.skill_placeholder import build_placeholder
    from openviking.privacy.skill_restore import restore_skill_content

    ctx = RequestContext(user=service.user, role=Role.USER)
    processor = service._skill_processor
    privacy = service.privacy_configs
    first = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    second = "AKLT" + "xY7zA9bC2dE4fG6hJ8kL0mN1pQ3sT5vW"
    fresh = "ghp_" + "pQ3sT5vWxY7zA9bC2dE4fG6hJ8kL0mN1"
    explicit = build_placeholder("final-refs", "kept0001")
    skill = {
        "name": "final-refs",
        "content": f"标题🙂 `{first}`\n{second}\n{fresh}\n重复：{fresh}\n显式：{explicit}",
    }
    original = skill["content"]
    await privacy.upsert(ctx, "skill", "final-refs", {"kept0001": "before-rotation"})

    preparation = await processor.prepare_skill_privacy(skill, ctx)

    # Changes after preparation must participate in final field selection.
    await privacy.upsert(ctx, "credential", "late", {"key": first})
    await privacy.upsert(
        ctx, "skill", "final-refs", {"kept0001": "after-rotation", "local001": second}
    )
    ids = iter(["kept0001", "local001", "new00001"])
    monkeypatch.setattr("openviking.utils.skill_processor.token_urlsafe", lambda _: next(ids))
    result = await processor.apply_skill_privacy(
        skill, preparation, ctx, change_reason="final references", delete_if_empty=False
    )
    current = await privacy.get_current(ctx, "skill", "final-refs")
    assert current.values == {"kept0001": "after-rotation", "local001": second, "new00001": fresh}
    assert result["content"] == original.replace(
        first, build_privacy_field_reference(ctx.user.user_id, "credential", "late", "key")
    ).replace(
        second, build_privacy_field_reference(ctx.user.user_id, "skill", "final-refs", "local001")
    ).replace(fresh, build_placeholder("final-refs", "new00001"))
    assert await restore_skill_content(
        privacy, result["content"], ctx, skill_name="final-refs"
    ) == original.replace(explicit, "after-rotation")


async def test_skill_rule_and_vlm_have_independent_discovery_paths(service, monkeypatch):
    import json
    from unittest.mock import AsyncMock

    ctx = RequestContext(user=service.user, role=Role.USER)
    processor = service._skill_processor
    selected = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    other = "AKLT" + "xY7zA9bC2dE4fG6hJ8kL0mN1pQ3sT5vW"
    managed = "managed-input-value"
    await service.privacy_configs.upsert(ctx, "credential", "managed", {"key": managed})
    body = f'---\nexample: {managed}\n---\nkey="{selected}"\n重复 {selected}\nother={other}\n'
    model = SimpleNamespace(
        get_completion_async=AsyncMock(return_value=json.dumps({"values": {"selected": selected}}))
    )
    resolve = AsyncMock(return_value=model)
    monkeypatch.setattr(processor._vlm_resolver, "get_vlm", resolve)
    monkeypatch.setenv("OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD", "vlm")

    result = await processor.sanitize_skill_privacy({"name": "vlm-discovery", "content": body}, ctx)
    current = await service.privacy_configs.get_current(ctx, "skill", "vlm-discovery")
    assert list(current.values.values()) == [selected]
    assert selected not in result["content"] and other in result["content"]
    assert managed not in model.get_completion_async.await_args.args[0]

    monkeypatch.setenv("OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD", "rule")
    result = await processor.sanitize_skill_privacy(
        {"name": "rule-discovery", "content": body}, ctx
    )
    current = await service.privacy_configs.get_current(ctx, "skill", "rule-discovery")
    assert set(current.values.values()) == {selected, other}
    assert selected not in result["content"] and other not in result["content"]
