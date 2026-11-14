# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest
import yaml

from openviking.privacy.reference import build_privacy_field_reference
from openviking.privacy.scan import scan_privacy_content
from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import patch_user_config
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.abstract_overview import parse_abstract_overview, render_abstract_overview
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError


@pytest.fixture
async def owner(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"privacy": {"mode": "registered"}}),
    )
    return ctx


@pytest.fixture
async def session_root(service, owner):
    session = await service.sessions.create(owner, "structured")
    return session.uri


@pytest.mark.parametrize("suffix", ["json", "yaml", "jsonl"])
async def test_structured_content_write_read_scan_and_rotation(
    service, owner, session_root, suffix
):
    secret = 'pa"ss\\word\n下一行'
    pem = "-----BEGIN PRIVATE KEY-----\nYWJjZGVmZw==\n-----END PRIVATE KEY-----"
    excluded = "public-example-value"
    row = {
        secret: secret,
        "pem": pem,
        "note": "a\u0085b\u2028c\u2029d",
        "items": [42, False, None],
        "public": excluded,
    }
    rows = [row, {"key": secret}]
    if suffix == "yaml":
        source = yaml.safe_dump_all(rows, explicit_start=True, allow_unicode=False)

        def decode(value):
            return list(yaml.safe_load_all(value))
    elif suffix == "jsonl":
        source = "\r\n".join(json.dumps(value, ensure_ascii=False) for value in rows) + "\r\n"

        def decode(value):
            return [json.loads(line) for line in value.split("\n") if line.strip()]
    else:
        source = json.dumps(rows, ensure_ascii=False)
        decode = json.loads
    uri = "viking://resources/structured/config." + suffix
    await service.viking_fs.write_file(uri, source, ctx=owner)
    await service.privacy_configs.upsert(
        owner, "credential", "structured", {"key": secret, "pem": pem, "public": excluded}
    )
    await patch_user_config(
        service.viking_fs,
        owner,
        UserSettingsPatch.model_validate(
            {"privacy": {"mode": "auto_strict", "never_mask": [excluded]}}
        ),
    )
    reference = build_privacy_field_reference(owner.user.user_id, "credential", "structured", "key")
    pem_reference = build_privacy_field_reference(
        owner.user.user_id, "credential", "structured", "pem"
    )
    expected = [{**row, secret: reference, "pem": pem_reference}, {"key": reference}]
    session_uri = session_root + "/config." + suffix
    await service.viking_fs.write_file(session_uri, source, ctx=owner)
    assert decode(await service.viking_fs.read_file(session_uri, ctx=owner)) == expected
    assert decode(await service.fs.read_visible(session_uri, ctx=owner)) == expected
    assert decode(await service.fs.read(session_uri, ctx=owner)) == rows
    assert decode(await service.fs.read_visible(uri, ctx=owner)) == expected
    assert decode(await service.viking_fs.read_file(uri, ctx=owner)) == rows

    await patch_user_config(
        service.viking_fs, owner, UserSettingsPatch.model_validate({"privacy": {"mode": "off"}})
    )
    report = await scan_privacy_content(
        service=service, ctx=owner, sanitizer=service.privacy_sanitizer, uri=uri, fix=True
    )
    assert report["repaired_files"] == 1
    assert decode(await service.viking_fs.read_file(uri, ctx=owner)) == expected
    rotated = 'rotated"key\\path\n下一行🙂\u2028'
    await service.privacy_configs.patch_values(owner, "credential", "structured", {"key": rotated})
    assert decode(await service.fs.read(session_uri, ctx=owner)) == [
        {**row, secret: rotated},
        {"key": rotated},
    ]
    assert decode(await service.fs.read(uri, ctx=owner)) == expected


@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "display: &label DemoLabel\nlookup: {*label: 23}\n",
            {"display": None, "lookup": {"DemoLabel": 23}},
        ),
        ("? &label DemoLabel\n: 23\ndisplay: *label\n", {"DemoLabel": 23, "display": None}),
        ("display: DemoLabel\nlookup: {public: 23}\n", {"display": None, "lookup": {"public": 23}}),
    ],
)
async def test_yaml_alias_keys_survive_write_and_default_read(
    service, owner, source, expected, bom
):
    source = ("\ufeff" if bom else "") + source
    await patch_user_config(
        service.viking_fs,
        owner,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
    await service.privacy_configs.upsert(owner, "credential", "label", {"key": "DemoLabel"})
    reference = build_privacy_field_reference(owner.user.user_id, "credential", "label", "key")
    expected = {**expected, "display": reference}
    uri = "viking://resources/alias/config.yaml"
    await service.viking_fs.write_file_bytes(uri, source.encode(), ctx=owner)
    assert yaml.safe_load(await service.fs.read_visible(uri, ctx=owner)) == expected
    assert await service.viking_fs.read_file(uri, ctx=owner) == source
    await service.fs.write(uri, source, ctx=owner, wait=True)
    stored = await service.viking_fs.read_file(uri, ctx=owner)
    assert yaml.safe_load(stored) == expected
    assert yaml.safe_load(await service.fs.read_visible(uri, ctx=owner)) == expected
    assert await service.fs.read(uri, ctx=owner) == stored


@pytest.mark.parametrize("path", ["content", "batch", "storage"])
@pytest.mark.parametrize("mode", ["auto", "auto_strict"])
async def test_ordinary_append_protection_across_write_paths(service, owner, path, mode):
    await patch_user_config(
        service.viking_fs, owner, UserSettingsPatch.model_validate({"privacy": {"mode": mode}})
    )
    secret = "registered-private-value"
    uri = "viking://resources/append/" + path + ".txt"
    await service.privacy_configs.upsert(owner, "credential", "append", {"key": secret})
    await service.fs.write(uri, "key=" + secret[:10], ctx=owner, wait=True)
    if path == "batch":
        await service.fs.batch_write(
            root_uri="viking://resources/append",
            operations=[{"uri": uri, "content": secret[10:], "mode": "append"}],
            ctx=owner,
            wait=False,
        )
    elif path == "storage":
        await service.viking_fs.append_file(uri, secret[10:], ctx=owner)
    else:
        await service.fs.write(uri, secret[10:], mode="append", ctx=owner, wait=False)
    stored = await service.viking_fs.read_file(uri, ctx=owner)
    reference = build_privacy_field_reference(owner.user.user_id, "credential", "append", "key")
    expected = "key=" + (reference if mode == "auto_strict" else secret)
    assert stored == expected


async def test_strict_manual_resolution_protects_retained_conflict_history(service, owner):
    secret = "registered-history-value"
    uri = "viking://resources/conflicts/history.md"
    record = {"state": "unfixed", "match_text": "conflicting content", "old_value": secret}
    source = (
        "# Original\n\nconflicting content\n\nAdditional context.\n\n<!-- CONFLICTS_CONTENT\n"
        + json.dumps(record)
        + "\n-->"
    )
    await service.fs.write(uri, source, ctx=owner, wait=True)
    await service.privacy_configs.upsert(owner, "credential", "history", {"key": secret})
    await patch_user_config(
        service.viking_fs,
        owner,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
    edited = "# Original\n\nresolved content\n\nAdditional context.\n\n"
    await service.fs.write(
        uri,
        edited.encode(),
        ctx=owner,
        resolve_conflicts=True,
        wait=True,
    )
    stored = await service.viking_fs.read_file(uri, ctx=owner)
    payload = stored.split("<!-- CONFLICTS_CONTENT", 1)[1].split("-->", 1)[0]
    history = json.loads(payload)
    assert history["state"] == "fixed" and history["match_text"] == "resolved content"
    assert history["old_value"] == build_privacy_field_reference(
        owner.user.user_id, "credential", "history", "key"
    )


@pytest.mark.parametrize("scope", ["resource", "skill"])
async def test_privacy_scan_skips_generated_sidecars(service, owner, scope):
    secret = "registered-marker"
    root = (
        "viking://resources/" + secret
        if scope == "resource"
        else f"viking://user/{owner.user.user_id}/skills/{secret}"
    )
    sources = {
        ".abstract.md": render_abstract_overview(0, root, "body " + secret),
        ".overview.md": render_abstract_overview(1, root, "").rstrip("\n"),
    }
    for name, source in sources.items():
        await service.viking_fs.write_file_bytes(root + "/" + name, source.encode(), ctx=owner)
    await service.privacy_configs.upsert(owner, "credential", "sidecar", {"key": secret})
    for target in [root] + [root + "/" + name for name in sources]:
        report = await scan_privacy_content(
            service=service, ctx=owner, sanitizer=service.privacy_sanitizer, uri=target, fix=True
        )
        count = len(sources) if target == root else 1
        assert report["skipped_files"] == {"internal_metadata": count}
        assert report["reindex"] == []
    for name, source in sources.items():
        assert await service.viking_fs.read_file(root + "/" + name, ctx=owner) == source


@pytest.mark.parametrize("kind", ["memory", "sidecar"])
async def test_ordinary_append_preserves_body_and_metadata(service, owner, kind):
    await patch_user_config(
        service.viking_fs,
        owner,
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto_strict"}}),
    )
    secret = "registered-private-value"
    if kind == "memory":
        uri = f"viking://user/{owner.user.user_space_name()}/memories/preferences/append.md"
        original = MemoryFile(content=secret[:10], extra_fields={"marker": secret})
        source = MemoryFileUtils.write(original)
    else:
        root = "viking://resources/" + secret
        uri = root + "/.overview.md"
        source = render_abstract_overview(1, root, secret[:10]).rstrip("\n")
    await service.viking_fs.write_file_bytes(uri, source.encode("utf-8"), ctx=owner)
    await service.privacy_configs.upsert(owner, "credential", "append", {"key": secret})
    await service.fs.write(uri, secret[10:], mode="append", ctx=owner, wait=False)
    stored = await service.viking_fs.read_file(uri, ctx=owner)
    if kind == "memory":
        document = MemoryFileUtils.read(stored, uri=uri)
        assert document.extra_fields["marker"] == secret
        body = document.content
    else:
        document = parse_abstract_overview(stored)
        assert document.metadata == parse_abstract_overview(source).metadata
        body = document.body
    reference = build_privacy_field_reference(owner.user.user_id, "credential", "append", "key")
    assert body.strip() == reference


async def test_write_budget_failure_keeps_whole_document_but_visible_read_fails(
    service, owner, session_root
):
    await service.privacy_configs.upsert(owner, "credential", "budget", {"key": "private-value"})
    service.privacy_sanitizer._max_replacements = 2
    uri = session_root + "/budget.json"
    source = json.dumps(["private-value"] * 3)
    await service.viking_fs.write_file(uri, source, ctx=owner)
    assert await service.viking_fs.read_file(uri, ctx=owner) == source
    with pytest.raises(ResourceExhaustedError, match="replacement limit"):
        await service.fs.read_visible(uri, ctx=owner)


async def test_structure_guard_rolls_back_write_and_scan_never_repairs_invalid_output(
    service, owner, session_root, monkeypatch
):
    await service.privacy_configs.upsert(owner, "credential", "guard", {"key": "private-value"})
    uri = session_root + "/guard.json"
    source = '["private-value"]'
    encode = json.dumps

    def broken_encoder(value, *args, **kwargs):
        if isinstance(value, str) and value.startswith("{{ov_privacy:"):
            return "42"
        return encode(value, *args, **kwargs)

    monkeypatch.setattr("openviking.privacy.content.json.dumps", broken_encoder)
    await service.viking_fs.write_file(uri, source, ctx=owner)
    assert await service.viking_fs.read_file(uri, ctx=owner) == source

    with pytest.raises(InvalidArgumentError, match="document structure"):
        await service.fs.read_visible(uri, ctx=owner)
    ordinary_uri = "viking://resources/guard/config.json"
    await service.fs.write(ordinary_uri, source, ctx=owner, wait=True)
    report = await scan_privacy_content(
        service=service, ctx=owner, sanitizer=service.privacy_sanitizer, uri=ordinary_uri, fix=True
    )
    assert report["skipped_files"] == {"privacy_structure_changed": 1}
    assert await service.viking_fs.read_file(ordinary_uri, ctx=owner) == source
