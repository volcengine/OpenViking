# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.privacy.reference import build_privacy_field_reference
from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import patch_user_config
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
async def enabled_privacy(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    await patch_user_config(
        service.viking_fs, ctx, UserSettingsPatch.model_validate({"privacy": {"mode": "auto"}})
    )


async def test_ordinary_writes_and_reads_follow_matching_scope(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = "registered-custom-value"
    await service.privacy_configs.upsert(ctx, "credential", "external-system", {"key": secret})
    await service.privacy_configs.upsert(ctx, "skill", "legacy", {"path": "viking://agent/skills"})
    uri = "viking://resources/privacy-sanitized.md"
    original = f"Shared skill directory: viking://agent/skills\ncredential={secret}"
    await service.fs.write(uri, original, ctx=ctx, wait=True)
    assert await service.fs.read_visible(uri, ctx=ctx) == original

    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
    reference = build_privacy_field_reference(
        ctx.user.user_id, "credential", "external-system", "key"
    )
    # Appending must protect the final value even when it spans both writes.
    await service.fs.write(uri, original[:-10], mode="replace", ctx=ctx, wait=True)
    await service.fs.write(uri, secret[-10:], mode="append", ctx=ctx, wait=True)
    expected = original.replace(secret, reference)
    assert await service.viking_fs.read_file(uri, ctx=ctx) == expected
    assert await service.fs.read_visible(uri, ctx=ctx) == expected
    assert await service.fs.read(uri, ctx=ctx) == expected


async def test_skill_preserves_metadata_and_protects_body_through_add_update_and_read(
    service, client, monkeypatch
):
    from openviking.core.skill_loader import SkillLoader
    from openviking.storage.abstract_overview import parse_abstract_overview

    monkeypatch.delenv("OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD", raising=False)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await patch_user_config(
        service.viking_fs, ctx, UserSettingsPatch.model_validate({"privacy": {"mode": "auto"}})
    )
    prefix = "sk-ant-api03-aB3dE5gH7jK9mN2pQ4sT"
    secret = prefix + "-v2"
    description = f"metadata managed-secret and {secret}"
    await service.privacy_configs.upsert(ctx, "credential", "managed", {"key": "managed-secret"})

    await service.privacy_configs.upsert(ctx, "credential", "prefix", {"key": prefix})
    for verb, label in [("post", "initial"), ("put", "updated")]:
        response = await getattr(client, verb)(
            "/api/v1/skills" + ("/metadata-skill" if verb == "put" else ""),
            json={
                "data": SkillLoader.to_skill_md(
                    {
                        "name": "metadata-skill",
                        "description": description,
                        "content": f'{label}🙂 managed-secret\napi_key="{secret}"',
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
        current = await service.privacy_configs.get_current(ctx, "skill", "metadata-skill")
        assert list(current.values.values()) == [secret]
        assert current.version == 1
        # Rotating a registered prefix must not change the distinct, longer credential.
        await service.privacy_configs.patch_values(
            ctx, "credential", "prefix", {"key": "rotated-prefix"}
        )
        assert secret in await service.fs.read(root + "/SKILL.md", ctx=ctx)


async def test_ambiguous_credential_does_not_rewrite_ordinary_content(service, monkeypatch):
    from openviking.privacy.scan import scan_privacy_content

    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
    ctx = RequestContext(user=service.user, role=Role.USER)
    duplicate = "same-registered-value"
    for target in ("first", "second"):
        await service.privacy_configs.upsert(ctx, "credential", target, {"key": duplicate})
    uri = "viking://resources/ambiguous.md"
    await service.fs.write(uri, duplicate, ctx=ctx, wait=True)
    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
    with pytest.raises(InvalidArgumentError, match="mapped to multiple fields"):
        await service.fs.read_visible(uri, ctx=ctx)
    with pytest.raises(InvalidArgumentError, match="mapped to multiple fields"):
        await service.fs.write(uri, duplicate + " updated", ctx=ctx, wait=True)
    with pytest.raises(InvalidArgumentError, match="mapped to multiple fields") as exc_info:
        await scan_privacy_content(
            service=service, ctx=ctx, sanitizer=service.privacy_sanitizer, uri=uri, fix=True
        )
    assert len(exc_info.value.details["candidate_references"][0]) == 2
    assert duplicate not in str(exc_info.value.details)
    assert await service.viking_fs.read_file(uri, ctx=ctx) == duplicate


async def test_sanitizer_uses_only_the_writing_users_privacy_catalog(service):
    owner = RequestContext(user=service.user, role=Role.USER)
    other = RequestContext(
        user=UserIdentifier(owner.account_id, "other-writer"),
        role=Role.USER,
    )
    owner_secret = "owner-only-registered-value"
    await service.initialize_user_directories(other)
    await patch_user_config(
        service.viking_fs,
        other,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
    await service.privacy_configs.upsert(
        owner,
        "credential",
        "owner-service",
        {"key": owner_secret},
        updated_by=owner.user.user_id,
    )

    uri = "viking://user/other-writer/sessions/catalog-scope/tool-results/read/output.txt"
    await service.viking_fs.write_file(uri, owner_secret, ctx=other)
    assert await service.viking_fs.read_file(uri, ctx=other) == owner_secret


async def test_visible_read_sanitizes_legacy_session_content(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    secret = 'quote"secret\\path\n下一行'
    await service.privacy_configs.upsert(ctx, "credential", "legacy-service", {"key": secret})
    uri = f"viking://user/{ctx.user.user_id}/sessions/legacy/tool-results/read/output.json"
    original = {secret: secret, "items": [42, False, None]}
    source = json.dumps(original, ensure_ascii=False)
    await service.viking_fs.write_file_bytes(uri, source.encode("utf-8"), ctx=ctx)
    reference = build_privacy_field_reference(
        ctx.user.user_id, "credential", "legacy-service", "key"
    )
    protected = {secret: reference, "items": [42, False, None]}
    assert json.loads(await service.fs.read_visible(uri, ctx=ctx)) == protected
    assert await service.viking_fs.read_file(uri, ctx=ctx) == source

    await service.viking_fs.write_file(uri, source, ctx=ctx)
    assert json.loads(await service.viking_fs.read_file(uri, ctx=ctx)) == protected
    rotated = 'rotated"key\\path\n下一行🙂'
    await service.privacy_configs.patch_values(
        ctx, "credential", "legacy-service", {"key": rotated}
    )
    assert json.loads(await service.fs.read(uri, ctx=ctx)) == {**original, secret: rotated}


async def test_resource_import_follows_matching_scope(service, tmp_path):
    ctx = RequestContext(user=service.user, role=Role.USER)
    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
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
    expected = source.read_text(encoding="utf-8").strip()
    expected = expected.replace(
        secret,
        build_privacy_field_reference(ctx.user.user_id, "credential", "import-service", "custom"),
    )
    assert secret not in stored
    assert expected in stored
